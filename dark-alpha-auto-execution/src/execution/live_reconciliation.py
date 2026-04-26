"""Startup reconciliation for Gate 2 live testnet execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from ulid import ULID

from safety.kill_switch import KillSwitch, get_kill_switch
from storage.db import get_db

from .binance_testnet_broker import (
    BinanceFuturesClient,
    BinanceSignedClient,
    _base_url_for_environment,
    normalize_symbol,
)
from .live_order_sync import LiveOrderStatusSync
from .live_safety import load_live_execution_config

log = structlog.get_logger(__name__)

_LOCAL_ACTIVE_ORDER_STATUSES = {"submitted", "acknowledged"}


@dataclass(frozen=True)
class SymbolReconciliation:
    symbol: str
    status: str
    mismatches: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReconciliationResult:
    run_id: str
    status: str
    symbols: list[SymbolReconciliation]

    @property
    def mismatches(self) -> list[str]:
        out: list[str] = []
        for symbol in self.symbols:
            out.extend(f"{symbol.symbol}:{item}" for item in symbol.mismatches)
        return out


class LiveReconciler:
    def __init__(
        self,
        *,
        client: BinanceFuturesClient | None = None,
        db_path: Path | None = None,
        kill_switch: KillSwitch | None = None,
        order_sync: LiveOrderStatusSync | None = None,
    ) -> None:
        config = load_live_execution_config()
        self._client = client or BinanceSignedClient(
            base_url=_base_url_for_environment(config.environment),
            environment=config.environment,
        )
        self._db_path = db_path
        self._kill_switch = kill_switch or get_kill_switch()
        self._order_sync = order_sync or LiveOrderStatusSync(client=self._client, db_path=db_path)

    def run(self, symbols: list[str]) -> ReconciliationResult:
        run_id = str(ULID())
        self._record_run(run_id, "started", {"symbols": symbols})
        try:
            symbol_results = [self._reconcile_symbol(symbol) for symbol in symbols]
            status = "mismatch" if any(r.mismatches for r in symbol_results) else "ok"
            result = ReconciliationResult(run_id=run_id, status=status, symbols=symbol_results)
            self._record_run(run_id, status, _result_details(result))
            if status == "mismatch":
                reason = "live_reconciliation_mismatch:" + ";".join(result.mismatches)
                self._kill_switch.activate(reason=reason[:500])
            return result
        except Exception as exc:
            self._record_run(run_id, "failed", {"error": str(exc)})
            self._kill_switch.activate(reason=f"live_reconciliation_failed:{exc}"[:500])
            raise

    def run_for_local_symbols(self) -> ReconciliationResult:
        return self.run(self.local_symbols())

    def local_symbols(self) -> list[str]:
        symbols: set[str] = set()
        with get_db(self._db_path) as conn:
            order_rows = conn.execute(
                """
                SELECT DISTINCT symbol
                  FROM order_idempotency
                 WHERE status IN ('submitted', 'acknowledged')
                """
            ).fetchall()
            position_rows = conn.execute(
                """
                SELECT DISTINCT symbol
                  FROM positions
                 WHERE shadow_mode=0
                   AND status IN ('pending','open','partial')
                """
            ).fetchall()
        symbols.update(str(row["symbol"]) for row in order_rows)
        symbols.update(str(row["symbol"]) for row in position_rows)
        return sorted(symbols)

    def _reconcile_symbol(self, symbol: str) -> SymbolReconciliation:
        self._order_sync.sync_symbol(symbol)

        local_ids = self._local_active_client_order_ids(symbol)
        exchange_orders = self._client.open_orders(symbol)
        exchange_ids = {
            str(row.get("clientOrderId") or row.get("newClientOrderId") or "")
            for row in exchange_orders
        }
        exchange_algo_orders = self._client.open_algo_orders(symbol)
        exchange_ids.update(
            str(row.get("clientAlgoId") or "")
            for row in exchange_algo_orders
        )
        exchange_ids.discard("")

        mismatches: list[str] = []
        unexpected = sorted(oid for oid in exchange_ids if oid.startswith("DA") and oid not in local_ids)
        if unexpected:
            mismatches.append(f"unexpected_exchange_orders={','.join(unexpected)}")

        missing = sorted(oid for oid in local_ids if oid not in exchange_ids)
        if missing:
            mismatches.append(f"local_orders_missing_on_exchange={','.join(missing)}")

        position_amt = self._exchange_position_amount(symbol)
        local_position_amt = self._local_live_position_amount(symbol)
        if abs(position_amt) > 0 and abs(local_position_amt) == 0:
            mismatches.append(f"exchange_position_without_local_position={position_amt}")
        elif abs(position_amt) == 0 and abs(local_position_amt) > 0:
            mismatches.append(f"local_position_missing_on_exchange={local_position_amt}")
        elif abs(position_amt - local_position_amt) > 1e-12:
            mismatches.append(f"position_amount_mismatch=exchange:{position_amt},local:{local_position_amt}")

        status = "mismatch" if mismatches else "ok"
        return SymbolReconciliation(symbol=symbol, status=status, mismatches=mismatches)

    def _local_active_client_order_ids(self, symbol: str) -> set[str]:
        normalized = normalize_symbol(symbol)
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT client_order_id, symbol
                  FROM order_idempotency
                 WHERE status IN ('submitted', 'acknowledged')
                """
            ).fetchall()
        return {
            str(row["client_order_id"])
            for row in rows
            if normalize_symbol(str(row["symbol"])) == normalized
        }

    def _exchange_position_amount(self, symbol: str) -> float:
        amount = 0.0
        for row in self._client.position_risk(symbol):
            amount += _float(row.get("positionAmt"))
        return amount

    def _local_live_position_amount(self, symbol: str) -> float:
        normalized = normalize_symbol(symbol)
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT symbol, direction, filled_quantity
                  FROM positions
                 WHERE shadow_mode=0
                   AND status IN ('pending','open','partial')
                """
            ).fetchall()
        amount = 0.0
        for row in rows:
            if normalize_symbol(str(row["symbol"])) != normalized:
                continue
            qty = _float(row["filled_quantity"])
            amount += qty if row["direction"] == "long" else -qty
        return amount

    def _record_run(self, run_id: str, status: str, details: dict[str, object]) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reconciliation_runs (run_id, status, details)
                VALUES (?, ?, ?)
                """,
                (run_id, status, json.dumps(details, sort_keys=True)),
            )
            conn.commit()
        log.info("live_reconciliation.recorded", run_id=run_id, status=status)


def _result_details(result: ReconciliationResult) -> dict[str, object]:
    return {
        "status": result.status,
        "symbols": [
            {
                "symbol": symbol.symbol,
                "status": symbol.status,
                "mismatches": symbol.mismatches,
            }
            for symbol in result.symbols
        ],
    }


def _float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
