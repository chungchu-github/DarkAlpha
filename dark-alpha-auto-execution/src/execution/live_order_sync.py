"""Live testnet order status sync.

This is intentionally polling-based. WebSocket user-data streams can be added
later, but Gate 2 needs a simple, auditable path first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from storage.db import get_db
from strategy.schemas import ExecutionTicket

from .binance_testnet_broker import (
    BinanceFuturesClient,
    BinanceSignedClient,
    LiveBrokerError,
    _base_url_for_environment,
    normalize_symbol,
)
from .live_safety import load_live_execution_config
from .position_manager import PositionManager

log = structlog.get_logger(__name__)

# Task 8 — 'reserved' must be syncable so timed-out / partial submissions can
# be healed by polling the exchange instead of leaking forever.
_OPEN_IDEMPOTENCY_STATUSES = {"reserved", "submitted", "acknowledged"}

# Substrings Binance returns for "order not found" (-2013, -1102) — sync treats
# these as confirmation that the order never reached the matching engine.
_NOT_FOUND_MARKERS = ("-2013", "order does not exist", "no such order")


@dataclass(frozen=True)
class OrderSyncResult:
    client_order_id: str
    exchange_status: str
    local_status: str
    filled_quantity: float
    average_price: float | None


class LiveOrderStatusSync:
    def __init__(
        self,
        *,
        client: BinanceFuturesClient | None = None,
        manager: PositionManager | None = None,
        db_path: Path | None = None,
    ) -> None:
        config = load_live_execution_config()
        self._client = client or BinanceSignedClient(
            base_url=_base_url_for_environment(config.environment),
            environment=config.environment,
        )
        self._manager = manager or PositionManager(db_path=db_path)
        self._db_path = db_path

    def sync_symbol(self, symbol: str) -> list[OrderSyncResult]:
        rows = self._local_open_rows(symbol)
        results: list[OrderSyncResult] = []
        for row in rows:
            cid = str(row["client_order_id"])
            sym = str(row["symbol"])
            role = str(row["order_role"])
            try:
                raw = (
                    self._client.query_algo_order(sym, cid)
                    if role in {"stop", "take_profit"}
                    else self._client.query_order(sym, cid)
                )
            except LiveBrokerError as exc:
                # Task 8 — exchange has no record of this clientOrderId, which
                # is the recovery path for a 'reserved' row whose submit timed
                # out before any ack. Mark it 'rejected' so future dispatch is
                # unblocked. Any other broker error is re-raised so ops sees it.
                if _is_order_not_found(str(exc)):
                    result = self._mark_not_found(cid)
                    results.append(result)
                    log.info(
                        "live_order_sync.order_not_found",
                        symbol=sym,
                        client_order_id=cid,
                        previous_status=str(row.get("status") or ""),
                    )
                    continue
                raise
            result = self._apply_exchange_status(cid, raw)
            results.append(result)
        if results:
            log.info("live_order_sync.symbol", symbol=symbol, count=len(results))
        return results

    def sync_all(self) -> list[OrderSyncResult]:
        symbols = self._local_symbols()
        results: list[OrderSyncResult] = []
        for symbol in symbols:
            results.extend(self.sync_symbol(symbol))
        return results

    def _local_symbols(self) -> list[str]:
        placeholders = ",".join("?" for _ in _OPEN_IDEMPOTENCY_STATUSES)
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT symbol
                  FROM order_idempotency
                 WHERE status IN ({placeholders})
                """,
                tuple(_OPEN_IDEMPOTENCY_STATUSES),
            ).fetchall()
        return [str(row["symbol"]) for row in rows]

    def _local_open_rows(self, symbol: str) -> list[dict[str, object]]:
        normalized = normalize_symbol(symbol)
        placeholders = ",".join("?" for _ in _OPEN_IDEMPOTENCY_STATUSES)
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT client_order_id, symbol, order_role, status
                  FROM order_idempotency
                 WHERE status IN ({placeholders})
                """,
                tuple(_OPEN_IDEMPOTENCY_STATUSES),
            ).fetchall()
        return [
            {
                "client_order_id": str(row["client_order_id"]),
                "symbol": str(row["symbol"]),
                "order_role": str(row["order_role"]),
                "status": str(row["status"]),
            }
            for row in rows
            if normalize_symbol(str(row["symbol"])) == normalized
        ]

    def _apply_exchange_status(
        self,
        client_order_id: str,
        payload: dict[str, object],
    ) -> OrderSyncResult:
        exchange_status = str(payload.get("status") or payload.get("algoStatus") or "").upper()
        local_status = _map_idempotency_status(exchange_status)
        filled_quantity = _float(payload.get("executedQty"))
        average_price = _avg_price(payload)
        order_status = exchange_status.lower() or local_status
        context = self._order_context(client_order_id)
        previous_filled = float(context.get("previous_filled") or 0.0) if context else 0.0
        fill_delta = max(filled_quantity - previous_filled, 0.0)

        with get_db(self._db_path) as conn:
            conn.execute(
                """
                UPDATE order_idempotency
                   SET status=?,
                       updated_at=datetime('now')
                 WHERE client_order_id=?
                """,
                (local_status, client_order_id),
            )
            conn.execute(
                """
                UPDATE orders
                   SET status=?,
                       filled_at=CASE WHEN ? IN ('filled','cancelled','rejected') THEN datetime('now') ELSE filled_at END,
                       fill_price=?,
                       fill_quantity=?
                 WHERE order_id=?
                """,
                (
                    order_status,
                    local_status,
                    average_price,
                    filled_quantity,
                    client_order_id,
                ),
            )
            conn.commit()

        if context is not None:
            ticket = ExecutionTicket.model_validate_json(str(context["ticket_payload"]))
            self._manager.apply_live_order_update(
                ticket=ticket,
                order_role=str(context["order_role"]),
                client_order_id=client_order_id,
                cumulative_filled=filled_quantity,
                fill_delta=fill_delta,
                average_price=average_price,
                local_status=local_status,
            )

        return OrderSyncResult(
            client_order_id=client_order_id,
            exchange_status=exchange_status,
            local_status=local_status,
            filled_quantity=filled_quantity,
            average_price=average_price,
        )

    def _mark_not_found(self, client_order_id: str) -> OrderSyncResult:
        """Mark a 'reserved' (or otherwise stuck) row as 'rejected'.

        Called by ``sync_symbol`` when the exchange replies that no order with
        this clientOrderId exists. The previous attempt failed before reaching
        the matching engine; treating the row as 'rejected' unblocks future
        dispatch and is consistent with the existing schema CHECK.
        """
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                UPDATE order_idempotency
                   SET status='rejected',
                       updated_at=datetime('now')
                 WHERE client_order_id=?
                """,
                (client_order_id,),
            )
            conn.execute(
                """
                UPDATE orders
                   SET status='rejected',
                       filled_at=datetime('now')
                 WHERE order_id=?
                """,
                (client_order_id,),
            )
            conn.commit()
        return OrderSyncResult(
            client_order_id=client_order_id,
            exchange_status="NOT_FOUND",
            local_status="rejected",
            filled_quantity=0.0,
            average_price=None,
        )

    def _order_context(self, client_order_id: str) -> dict[str, object] | None:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT oi.order_role,
                       et.payload AS ticket_payload,
                       COALESCE(o.fill_quantity, 0) AS previous_filled
                  FROM order_idempotency oi
                  JOIN execution_tickets et ON et.ticket_id = oi.ticket_id
                  LEFT JOIN orders o ON o.order_id = oi.client_order_id
                 WHERE oi.client_order_id=?
                """,
                (client_order_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "order_role": row["order_role"],
            "ticket_payload": row["ticket_payload"],
            "previous_filled": row["previous_filled"],
        }


def _is_order_not_found(error_text: str) -> bool:
    text = error_text.lower()
    return any(marker in text for marker in _NOT_FOUND_MARKERS)


def _map_idempotency_status(exchange_status: str) -> str:
    match exchange_status.upper():
        case "NEW" | "PARTIALLY_FILLED":
            return "acknowledged"
        case "FILLED":
            return "filled"
        case "CANCELED" | "EXPIRED":
            return "cancelled"
        case "REJECTED":
            return "rejected"
        case _:
            return "acknowledged"


def _avg_price(payload: dict[str, object]) -> float | None:
    avg = _float(payload.get("avgPrice"))
    if avg > 0:
        return avg
    qty = _float(payload.get("executedQty"))
    quote = _float(payload.get("cumQuote"))
    if qty > 0 and quote > 0:
        return quote / qty
    return None


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
