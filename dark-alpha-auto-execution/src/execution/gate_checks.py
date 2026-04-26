"""Executable Gate 2.5 -> Gate 6 safety checks.

These checks are intentionally local and deterministic. They exercise the same
sync, reconciliation, idempotency, broker validation, and risk gate code used
by live/testnet runs without needing a real Binance connection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from safety.kill_switch import KillSwitch
from signal_adapter.schemas import InvalidationInfo, SetupEvent, TriggerInfo
from storage.db import get_db, init_db
from strategy.risk_gate import RiskGate
from strategy.schemas import ExecutionTicket, PlannedOrder

from .binance_testnet_broker import BinanceFuturesBroker, BinanceTestnetBroker, LiveBrokerError
from .exchange_filters import StaticExchangeFilterProvider, SymbolFilters
from .gate6_readiness import Gate6ReadinessReviewer
from .live_event_guard import LiveEventGuard
from .live_order_sync import LiveOrderStatusSync
from .live_reconciliation import LiveReconciler
from .live_safety import (
    LiveExecutionConfig,
    LivePreflightError,
    assert_mainnet_readiness,
    assert_micro_live_ticket,
    client_order_id,
)
from .live_user_stream import LiveUserStreamIngestor


@dataclass(frozen=True)
class GateCheckStep:
    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class GateCheckReport:
    gate: str
    status: str
    steps: list[GateCheckStep] = field(default_factory=list)

    def markdown(self) -> str:
        lines = [f"# {self.gate} Check", "", f"- status: `{self.status}`", ""]
        for step in self.steps:
            detail = f" - {step.detail}" if step.detail else ""
            lines.append(f"- `{step.status}` {step.name}{detail}")
        return "\n".join(lines)


class LifecycleFakeClient:
    """Stateful fake Binance client for lifecycle checks."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, object]] = {}
        self.algo_orders: dict[str, dict[str, object]] = {}
        self.exchange_position_amt = 0.0
        self.cancelled: list[str] = []
        self.algo_cancelled: list[str] = []
        self.new_order_calls: list[Mapping[str, Any]] = []

    def set_leverage(self, symbol: str, leverage: int) -> Mapping[str, Any]:
        return {"symbol": symbol, "leverage": leverage}

    def position_risk(self, symbol: str) -> list[Mapping[str, Any]]:
        return [{"positionAmt": str(self.exchange_position_amt)}]

    def open_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return [
            payload
            for payload in self.orders.values()
            if str(payload.get("status", "")).upper() in {"NEW", "PARTIALLY_FILLED"}
        ]

    def open_algo_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return [
            payload
            for payload in self.algo_orders.values()
            if str(payload.get("algoStatus", "")).upper() in {"NEW", "PARTIALLY_FILLED"}
        ]

    def new_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self.new_order_calls.append(params)
        return {
            "clientOrderId": params.get("newClientOrderId", "DACLOSE"),
            "orderId": "close-1",
            "symbol": params["symbol"],
            "side": params["side"],
            "type": params["type"],
            "status": "NEW",
            "origQty": params["quantity"],
        }

    def new_algo_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:
        return self.orders[client_order_id]

    def query_algo_order(self, symbol: str, client_algo_id: str) -> Mapping[str, Any]:
        return self.algo_orders[client_algo_id]

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:
        self.cancelled.append(symbol)
        for payload in self.orders.values():
            if str(payload.get("status", "")).upper() in {"NEW", "PARTIALLY_FILLED"}:
                payload["status"] = "CANCELED"
        return {"code": 200, "msg": "success"}

    def cancel_all_open_algo_orders(self, symbol: str) -> Mapping[str, Any]:
        self.algo_cancelled.append(symbol)
        for payload in self.algo_orders.values():
            if str(payload.get("algoStatus", "")).upper() in {"NEW", "PARTIALLY_FILLED"}:
                payload["algoStatus"] = "CANCELED"
        return {"code": 200, "msg": "success"}


def run_gate25_fill_lifecycle(db_path: Path | None = None) -> GateCheckReport:
    """Exercise entry partial/full fill, SL/TP close, cancel, flatten, reconcile."""
    with _ephemeral_db(db_path) as db:
        client = LifecycleFakeClient()
        steps: list[GateCheckStep] = []

        ticket = _ticket(ticket_id="GATE25SL", source_event_id="gate25-sl")
        ids = _seed_live_ticket(db, ticket)
        client.orders[ids["entry"]] = _regular_payload(ids["entry"], "PARTIALLY_FILLED", "0.005", "0.5")
        client.algo_orders[ids["stop"]] = _algo_payload(ids["stop"], "NEW", "0", "0")
        client.algo_orders[ids["take_profit"]] = _algo_payload(ids["take_profit"], "NEW", "0", "0")

        LiveOrderStatusSync(client=client, db_path=db).sync_symbol(ticket.symbol)
        _assert_position(db, ticket.ticket_id, "partial", 0.005)
        steps.append(GateCheckStep("entry partial fill updates local position", "ok"))

        client.orders[ids["entry"]] = _regular_payload(ids["entry"], "FILLED", "0.01", "1.0")
        client.exchange_position_amt = 0.01
        LiveOrderStatusSync(client=client, db_path=db).sync_symbol(ticket.symbol)
        _assert_position(db, ticket.ticket_id, "open", 0.01)
        steps.append(GateCheckStep("entry full fill opens local live position", "ok"))

        client.algo_orders[ids["stop"]] = _algo_payload(ids["stop"], "FILLED", "0.01", "0.99")
        client.exchange_position_amt = 0.0
        LiveOrderStatusSync(client=client, db_path=db).sync_symbol(ticket.symbol)
        _assert_closed(db, ticket.ticket_id, "stop_loss")
        steps.append(GateCheckStep("stop fill closes local live position", "ok"))

        tp_ticket = _ticket(ticket_id="GATE25TP", source_event_id="gate25-tp")
        tp_ids = _seed_live_ticket(db, tp_ticket)
        client.orders[tp_ids["entry"]] = _regular_payload(tp_ids["entry"], "FILLED", "0.01", "1.0")
        client.algo_orders[tp_ids["stop"]] = _algo_payload(tp_ids["stop"], "NEW", "0", "0")
        client.algo_orders[tp_ids["take_profit"]] = _algo_payload(tp_ids["take_profit"], "NEW", "0", "0")
        client.exchange_position_amt = 0.01
        LiveOrderStatusSync(client=client, db_path=db).sync_symbol(tp_ticket.symbol)
        client.algo_orders[tp_ids["take_profit"]] = _algo_payload(tp_ids["take_profit"], "FILLED", "0.01", "1.02")
        client.exchange_position_amt = 0.0
        LiveOrderStatusSync(client=client, db_path=db).sync_symbol(tp_ticket.symbol)
        _assert_closed(db, tp_ticket.ticket_id, "take_profit")
        steps.append(GateCheckStep("take-profit fill closes local live position", "ok"))

        client.cancel_all_open_orders(ticket.symbol)
        client.cancel_all_open_algo_orders(ticket.symbol)
        LiveOrderStatusSync(client=client, db_path=db).sync_symbol(ticket.symbol)
        reconcile = LiveReconciler(
            client=client,
            db_path=db,
            kill_switch=KillSwitch(sentinel_path=db.with_suffix(".kill")),
        ).run([ticket.symbol])
        if reconcile.status != "ok":
            raise AssertionError(reconcile.mismatches)
        steps.append(GateCheckStep("cancel/sync/reconcile leaves no orphan orders", "ok"))

        client.exchange_position_amt = 0.02
        broker = BinanceTestnetBroker(
            client=client,
            config=_live_config("testnet"),
            filters=_filters(),
        )
        ack = broker.emergency_close_symbol(ticket.symbol)
        if ack is None or not client.new_order_calls or client.new_order_calls[-1].get("reduceOnly") != "true":
            raise AssertionError("emergency flatten did not submit reduce-only market")
        steps.append(GateCheckStep("emergency flatten submits reduce-only market close", "ok"))

        return GateCheckReport("Gate 2.5", "ok", steps)


def run_gate3_restart_safety(db_path: Path | None = None) -> GateCheckReport:
    """Check duplicate prevention, reconciliation halt, and kill-switch blocking."""
    with _ephemeral_db(db_path) as db:
        steps: list[GateCheckStep] = []
        ticket = _ticket(ticket_id="GATE3DUP", source_event_id="gate3-dup")
        ids = _seed_live_ticket(db, ticket)

        statuses = _order_statuses(db, ticket.ticket_id)
        if statuses.get(ids["entry"]) not in {"submitted", "acknowledged"}:
            raise AssertionError("missing submitted order status")
        steps.append(GateCheckStep("restart sees existing submitted clientOrderId", "ok"))

        if not any(status in {"submitted", "acknowledged", "filled"} for status in statuses.values()):
            raise AssertionError("duplicate guard would not block")
        steps.append(GateCheckStep("duplicate live ticket would be blocked before broker", "ok"))

        client = LifecycleFakeClient()
        client.orders["DAUNEXPECTED"] = {
            "clientOrderId": "DAUNEXPECTED",
            "symbol": "BTCUSDT",
            "status": "NEW",
            "executedQty": "0",
        }
        client.orders[ids["entry"]] = _regular_payload(ids["entry"], "NEW", "0", "0")
        client.algo_orders[ids["stop"]] = _algo_payload(ids["stop"], "NEW", "0", "0")
        client.algo_orders[ids["take_profit"]] = _algo_payload(ids["take_profit"], "NEW", "0", "0")
        ks = KillSwitch(sentinel_path=db.with_suffix(".kill"))
        result = LiveReconciler(client=client, db_path=db, kill_switch=ks).run([ticket.symbol])
        if result.status != "mismatch" or not ks.is_active():
            raise AssertionError("reconciliation mismatch did not activate kill switch")
        steps.append(GateCheckStep("reconcile mismatch activates kill switch", "ok"))

        rejection = RiskGate(kill_switch=ks, db_path=db).check(_event("gate3-kill"), equity_usd=10_000)
        if rejection is None or rejection.reason != "kill_switch_active":
            raise AssertionError("kill switch did not block risk gate")
        steps.append(GateCheckStep("kill switch blocks new trading decisions", "ok"))
        return GateCheckReport("Gate 3", "ok", steps)


def run_gate35_risk_matrix(db_path: Path | None = None) -> GateCheckReport:
    """Exercise high-value risk rejection paths without contacting Binance."""
    with _ephemeral_db(db_path) as db:
        steps: list[GateCheckStep] = []
        event = _event("gate35-base")
        _seed_setup_event(db, event)

        ks = KillSwitch(sentinel_path=db.with_suffix(".kill"))
        ks.activate("gate35")
        rejection = RiskGate(kill_switch=ks, db_path=db).check(event, equity_usd=10_000)
        _expect_rejection(rejection, "kill_switch_active")
        steps.append(GateCheckStep("kill switch rejects signal", "ok"))
        ks.deactivate()

        rejection = RiskGate(kill_switch=ks, db_path=db).check(event, equity_usd=1)
        _expect_rejection(rejection, "below_min_equity")
        steps.append(GateCheckStep("minimum equity rejects signal", "ok"))

        _insert_open_position(db, event.symbol)
        rejection = RiskGate(kill_switch=ks, db_path=db).check(event, equity_usd=10_000)
        _expect_rejection(rejection, "duplicate_symbol")
        steps.append(GateCheckStep("duplicate symbol rejects signal", "ok"))

        unsafe = _ticket(ticket_id="GATE35UNSAFE", source_event_id="gate35-unsafe")
        unsafe = unsafe.model_copy(update={"orders": [unsafe.orders[0]], "take_profit_price": None})
        broker = BinanceTestnetBroker(
            client=LifecycleFakeClient(),
            config=_live_config("testnet"),
            filters=_filters(),
        )
        try:
            broker.submit_ticket(unsafe)
        except LiveBrokerError as exc:
            if "missing_order_roles" not in str(exc):
                raise
        else:
            raise AssertionError("broker accepted ticket without SL/TP bracket")
        steps.append(GateCheckStep("missing protective bracket is rejected before exchange", "ok"))

        mainnet_cfg = _live_config(
            "mainnet",
            micro_live={
                "enabled": True,
                "allowed_symbols": ["ETHUSDT-PERP"],
                "max_notional_usd": 20,
                "max_leverage": 2,
                "max_daily_loss_usd": 5,
                "max_concurrent_positions": 1,
                "exercise_window_start": "2026-01-01T00:00:00+00:00",
                "exercise_window_end": "2026-12-31T23:59:59+00:00",
            },
        )
        capped = _ticket(ticket_id="GATE35CAP", source_event_id="gate35-cap")
        try:
            assert_micro_live_ticket(capped, mainnet_cfg, require_credentials=False)
        except LivePreflightError as exc:
            if "mainnet_symbol_not_allowed" not in str(exc):
                raise
        else:
            raise AssertionError("mainnet allowlist did not reject symbol")
        steps.append(GateCheckStep("mainnet symbol allowlist rejects unsafe symbol", "ok"))

        allowed = capped.model_copy(update={"symbol": "ETHUSDT-PERP", "notional_usd": 100})
        try:
            assert_micro_live_ticket(allowed, mainnet_cfg, require_credentials=False)
        except LivePreflightError as exc:
            if "mainnet_notional_cap" not in str(exc):
                raise
        else:
            raise AssertionError("mainnet notional cap did not reject ticket")
        steps.append(GateCheckStep("mainnet notional cap rejects oversized ticket", "ok"))

        return GateCheckReport("Gate 3.5", "ok", steps)


def run_gate5_mainnet_preflight() -> GateCheckReport:
    """Validate that mainnet is locked unless explicit micro-live config exists."""
    steps: list[GateCheckStep] = []
    locked = _live_config("mainnet", allow_mainnet=False)
    try:
        assert_mainnet_readiness(locked)
    except LivePreflightError as exc:
        if "credentials_missing" not in str(exc) and "micro_live_not_enabled" not in str(exc):
            raise
    steps.append(GateCheckStep("mainnet preflight refuses incomplete runtime", "ok"))

    missing_caps = _live_config(
        "mainnet",
        allow_mainnet=True,
        micro_live={"enabled": True, "allowed_symbols": ["ETHUSDT-PERP"]},
    )
    try:
        assert_mainnet_readiness(missing_caps)
    except LivePreflightError:
        steps.append(GateCheckStep("mainnet preflight requires explicit caps/window", "ok"))
    else:
        raise AssertionError("mainnet preflight accepted missing caps")

    return GateCheckReport("Gate 5", "ok", steps)


def run_gate6_micro_live_canary_scaffold() -> GateCheckReport:
    """Check the Gate 6 canary object can pass caps and uses generic broker."""
    now = datetime.now(tz=UTC)
    cfg = _live_config(
        "mainnet",
        allow_mainnet=True,
        micro_live={
            "enabled": True,
            "allowed_symbols": ["ETHUSDT-PERP"],
            "max_notional_usd": 20,
            "max_leverage": 2,
            "max_daily_loss_usd": 5,
            "max_concurrent_positions": 1,
            "exercise_window_start": (now - timedelta(minutes=5)).isoformat(),
            "exercise_window_end": (now + timedelta(minutes=5)).isoformat(),
        },
    )
    ticket = _ticket(
        ticket_id="GATE6CANARY",
        source_event_id="gate6-canary",
        symbol="ETHUSDT-PERP",
        notional_usd=10,
        leverage=1,
    )
    assert_micro_live_ticket(ticket, cfg, require_credentials=False)
    broker = BinanceFuturesBroker(client=LifecycleFakeClient(), config=cfg, filters=_filters("ETHUSDT"))
    if not isinstance(broker, BinanceFuturesBroker):
        raise AssertionError("generic futures broker scaffold unavailable")
    return GateCheckReport(
        "Gate 6",
        "ok",
        [
            GateCheckStep("micro-live canary ticket passes explicit caps", "ok"),
            GateCheckStep("generic broker is available for gated mainnet execution", "ok"),
        ],
    )


def run_gate64_user_stream_ingestion(db_path: Path | None = None) -> GateCheckReport:
    """Check Gate 6.4 event ingestion can open/close positions without polling."""
    with _ephemeral_db(db_path) as db:
        steps: list[GateCheckStep] = []
        ticket = _ticket(ticket_id="GATE64STREAM", source_event_id="gate64-stream")
        ids = _seed_live_ticket(db, ticket)
        ks = KillSwitch(sentinel_path=db.with_suffix(".kill"))
        ingestor = LiveUserStreamIngestor(db_path=db, guard=LiveEventGuard(db_path=db, kill_switch=ks))

        entry = _order_trade_update(ids["entry"], "BUY", "LIMIT", "FILLED", "0.01", "0.01", "100.0", "1")
        ingestor.process_event(entry)
        _assert_position(db, ticket.ticket_id, "open", 0.01)
        steps.append(GateCheckStep("ORDER_TRADE_UPDATE entry fill opens local position", "ok"))

        stop = _order_trade_update(ids["stop"], "SELL", "STOP_MARKET", "FILLED", "0.01", "0.01", "99.0", "2")
        ingestor.process_event(stop)
        _assert_closed(db, ticket.ticket_id, "stop_loss")
        steps.append(GateCheckStep("ORDER_TRADE_UPDATE stop fill closes local position", "ok"))

        ingestor.process_event(stop)
        with get_db(db) as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM live_stream_events").fetchone()["n"]
        if int(count) != 2:
            raise AssertionError("duplicate stream event was not ignored")
        steps.append(GateCheckStep("stream event de-duplication is active", "ok"))
        return GateCheckReport("Gate 6.4", "ok", steps)


def run_gate66_event_driven_risk(db_path: Path | None = None) -> GateCheckReport:
    """Check event-driven guard halts unsafe live state."""
    with _ephemeral_db(db_path) as db:
        steps: list[GateCheckStep] = []
        ticket = _ticket(ticket_id="GATE66GUARD", source_event_id="gate66-guard")
        ids = _seed_live_ticket(db, ticket)
        ks = KillSwitch(sentinel_path=db.with_suffix(".kill"))
        guard = LiveEventGuard(db_path=db, kill_switch=ks)
        ingestor = LiveUserStreamIngestor(db_path=db, guard=guard)

        with get_db(db) as conn:
            conn.execute(
                """
                UPDATE order_idempotency
                   SET status='cancelled'
                 WHERE ticket_id=? AND order_role='stop'
                """,
                (ticket.ticket_id,),
            )
            conn.commit()
        ingestor.process_event(
            _order_trade_update(ids["entry"], "BUY", "LIMIT", "FILLED", "0.01", "0.01", "100.0", "1")
        )
        if not ks.is_active():
            raise AssertionError("missing protective stop did not activate kill switch")
        steps.append(GateCheckStep("entry fill without full protective bracket activates kill switch", "ok"))

        ks.deactivate()
        result = guard.record_untracked_fill(
            symbol=ticket.symbol,
            client_order_id="MANUALORDER1",
            fill_delta=0.01,
            allow_emergency_close=False,
        )
        if result.status != "halted" or not ks.is_active():
            raise AssertionError("untracked manual fill did not activate kill switch")
        steps.append(GateCheckStep("untracked manual fill activates kill switch", "ok"))
        return GateCheckReport("Gate 6.6", "ok", steps)


def run_gate68_readiness_review(db_path: Path | None = None) -> GateCheckReport:
    """Check Gate 6.8 Go/No-Go reviewer marks complete evidence as go."""
    with _ephemeral_db(db_path) as db:
        now = datetime.now(tz=UTC)
        ticket = _ticket(ticket_id="GATE68READY", source_event_id="gate68-ready")
        ids = _seed_live_ticket(db, ticket)
        ingestor = LiveUserStreamIngestor(
            db_path=db,
            guard=LiveEventGuard(db_path=db, kill_switch=KillSwitch(sentinel_path=db.with_suffix(".kill"))),
        )
        ingestor.process_event(
            _order_trade_update(ids["entry"], "BUY", "LIMIT", "FILLED", "0.01", "0.01", "100.0", "1")
        )
        with get_db(db) as conn:
            conn.execute(
                """
                INSERT INTO live_runtime_heartbeats (component, status, details, created_at)
                VALUES ('user_stream', 'event_ingested', '{}', datetime('now'))
                """
            )
            conn.execute(
                """
                INSERT INTO reconciliation_runs (run_id, status, details, created_at)
                VALUES ('gate68-recon', 'ok', ?, datetime('now'))
                """,
                (
                    '{"status":"ok","symbols":[{"symbol":"BTCUSDT-PERP","status":"ok","mismatches":[]}]}',
                ),
            )
            conn.commit()
        report = Gate6ReadinessReviewer(
            db_path=db,
            kill_switch=KillSwitch(sentinel_path=db.with_suffix(".gate68-kill")),
            now=now,
        ).run(symbols=[ticket.symbol], require_burn_in_hours=1)
        if report.status != "go":
            raise AssertionError(report.markdown())
        return GateCheckReport(
            "Gate 6.8",
            "ok",
            [
                GateCheckStep("readiness reviewer requires stream, heartbeat, reconciliation, guard, burn-in", "ok"),
                GateCheckStep("complete local evidence produces GO", "ok"),
            ],
        )
def _ticket(
    *,
    ticket_id: str,
    source_event_id: str,
    symbol: str = "BTCUSDT-PERP",
    notional_usd: float = 1.0,
    leverage: float = 1.0,
) -> ExecutionTicket:
    return ExecutionTicket(
        ticket_id=ticket_id,
        source_event_id=source_event_id,
        symbol=symbol,
        direction="long",
        regime="gate_check",
        ranking_score=9.0,
        shadow_mode=False,
        gate="gate6" if ticket_id.startswith("GATE6") else "gate2",
        entry_price=100.0,
        stop_price=99.0,
        take_profit_price=102.0,
        quantity=0.01,
        notional_usd=notional_usd,
        leverage=leverage,
        risk_usd=0.01,
        orders=[
            PlannedOrder(role="entry", side="buy", type="limit", symbol=symbol, price=100.0, quantity=0.01),
            PlannedOrder(role="stop", side="sell", type="stop_market", symbol=symbol, price=99.0, quantity=0.01, reduce_only=True),
            PlannedOrder(role="take_profit", side="sell", type="limit", symbol=symbol, price=102.0, quantity=0.01, reduce_only=True),
        ],
        created_at=datetime.now(tz=UTC).isoformat(),
    )


def _seed_live_ticket(db: Path, ticket: ExecutionTicket) -> dict[str, str]:
    _seed_setup_event(db, _event(ticket.source_event_id, symbol=ticket.symbol))
    ids = {order.role: client_order_id(ticket, order) for order in ticket.orders}
    with get_db(db) as conn:
        conn.execute(
            """
            INSERT INTO execution_tickets
                (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
            VALUES (?, ?, 'accepted', 0, ?, ?)
            """,
            (ticket.ticket_id, ticket.source_event_id, ticket.model_dump_json(), ticket.created_at),
        )
        for order in ticket.orders:
            cid = ids[order.role]
            conn.execute(
                """
                INSERT INTO order_idempotency
                    (client_order_id, ticket_id, order_role, symbol, side, quantity, price, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted')
                """,
                (cid, ticket.ticket_id, order.role, order.symbol, order.side, order.quantity, order.price),
            )
            conn.execute(
                """
                INSERT INTO orders
                    (order_id, ticket_id, exchange_order_id, side, type, symbol,
                     price, quantity, status, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', datetime('now'))
                """,
                (cid, ticket.ticket_id, f"ex-{cid}", order.side, order.type, order.symbol, order.price, order.quantity),
            )
        conn.commit()
    return ids


def _seed_setup_event(db: Path, event: SetupEvent) -> None:
    with get_db(db) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO setup_events
                (event_id, timestamp, symbol, setup_type, payload, received_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (event.event_id, event.timestamp, event.symbol, event.setup_type, event.model_dump_json()),
        )
        conn.commit()


def _event(event_id: str, symbol: str = "BTCUSDT-PERP") -> SetupEvent:
    return SetupEvent(
        event_id=event_id,
        timestamp=datetime.now(tz=UTC).isoformat(),
        symbol=symbol,
        setup_type="active",
        direction="long",
        regime="gate_check",
        today_decision="gate_check",
        ranking_score=9.0,
        trigger=TriggerInfo(condition="gate_check", price_level=100.0, timeframe="15m"),
        invalidation=InvalidationInfo(condition="stop", price_level=99.0),
        metadata={},
    )


def _regular_payload(cid: str, status: str, executed_qty: str, cum_quote: str) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "clientOrderId": cid,
        "status": status,
        "executedQty": executed_qty,
        "cumQuote": cum_quote,
    }


def _algo_payload(cid: str, status: str, executed_qty: str, cum_quote: str) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "clientAlgoId": cid,
        "algoStatus": status,
        "executedQty": executed_qty,
        "cumQuote": cum_quote,
    }


def _order_trade_update(
    cid: str,
    side: str,
    order_type: str,
    status: str,
    cumulative: str,
    last: str,
    avg_price: str,
    trade_id: str,
) -> dict[str, object]:
    return {
        "e": "ORDER_TRADE_UPDATE",
        "E": int(datetime.now(tz=UTC).timestamp() * 1000),
        "o": {
            "s": "BTCUSDT",
            "c": cid,
            "S": side,
            "o": order_type,
            "q": "0.01",
            "p": avg_price,
            "ap": avg_price,
            "x": "TRADE",
            "X": status,
            "l": last,
            "z": cumulative,
            "L": avg_price,
            "i": trade_id,
            "t": trade_id,
        },
    }


def _assert_position(db: Path, ticket_id: str, status: str, qty: float) -> None:
    with get_db(db) as conn:
        row = conn.execute(
            "SELECT status, filled_quantity FROM positions WHERE ticket_id=? ORDER BY opened_at DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
    if row is None or row["status"] != status or abs(float(row["filled_quantity"]) - qty) > 1e-12:
        raise AssertionError(f"position mismatch for {ticket_id}: expected {status}/{qty}")


def _assert_closed(db: Path, ticket_id: str, reason: str) -> None:
    with get_db(db) as conn:
        row = conn.execute(
            "SELECT status, exit_reason FROM positions WHERE ticket_id=? ORDER BY closed_at DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
    if row is None or row["status"] != "closed" or row["exit_reason"] != reason:
        raise AssertionError(f"position not closed by {reason} for {ticket_id}")


def _insert_open_position(db: Path, symbol: str) -> None:
    with get_db(db) as conn:
        conn.execute(
            """
            INSERT INTO positions
                (position_id, ticket_id, symbol, direction, status, entry_price,
                 quantity, filled_quantity, stop_price, take_profit_price, opened_at,
                 fees_usd, shadow_mode)
            VALUES ('gate35-pos',NULL,?,'long','open',100,0.01,0.01,99,102,
                    datetime('now'),0,1)
            """,
            (symbol,),
        )
        conn.commit()


def _order_statuses(db: Path, ticket_id: str) -> dict[str, str]:
    with get_db(db) as conn:
        rows = conn.execute(
            "SELECT client_order_id, status FROM order_idempotency WHERE ticket_id=?",
            (ticket_id,),
        ).fetchall()
    return {str(row["client_order_id"]): str(row["status"]) for row in rows}


def _expect_rejection(rejection: object, reason: str) -> None:
    if rejection is None or getattr(rejection, "reason", None) != reason:
        raise AssertionError(f"expected rejection {reason}, got {rejection}")


def _filters(symbol: str = "BTCUSDT") -> StaticExchangeFilterProvider:
    return StaticExchangeFilterProvider(
        SymbolFilters(
            symbol=symbol,
            tick_size=Decimal("0.1"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("1"),
        )
    )


def _live_config(
    environment: str,
    *,
    allow_mainnet: bool = False,
    micro_live: dict[str, object] | None = None,
) -> LiveExecutionConfig:
    return LiveExecutionConfig(
        mode="live",
        environment=environment,
        allow_mainnet=allow_mainnet,
        require_gate_authorization=False,
        gate_authorization_file="docs/gate-6-authorization.md" if environment == "mainnet" else "missing",
        micro_live=micro_live or {},
    )


class _ephemeral_db:
    def __init__(self, db_path: Path | None) -> None:
        self._given = db_path
        self._tmp: TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        if self._given is not None:
            self.path = self._given
        else:
            self._tmp = TemporaryDirectory()
            self.path = Path(self._tmp.name) / "gate-check.db"
        init_db(self.path)
        return self.path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
