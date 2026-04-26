"""Tests for live testnet order status polling."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from execution.live_order_sync import LiveOrderStatusSync
from storage.db import get_db, init_db
from strategy.schemas import ExecutionTicket, PlannedOrder


class FakeClient:
    def __init__(self, status: str = "FILLED", executed_qty: str = "0.01") -> None:
        self.status = status
        self.executed_qty = executed_qty
        self.queried: list[tuple[str, str]] = []

    def set_leverage(self, symbol: str, leverage: int) -> Mapping[str, Any]:
        return {}

    def position_risk(self, symbol: str) -> list[Mapping[str, Any]]:
        return []

    def open_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return []

    def open_algo_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return []

    def new_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def new_algo_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:
        self.queried.append((symbol, client_order_id))
        return {
            "symbol": "BTCUSDT",
            "clientOrderId": client_order_id,
            "status": self.status,
            "executedQty": self.executed_qty,
            "cumQuote": "1.23",
        }

    def query_algo_order(self, symbol: str, client_algo_id: str) -> Mapping[str, Any]:
        return {
            "symbol": "BTCUSDT",
            "clientAlgoId": client_algo_id,
            "algoStatus": self.status,
            "executedQty": self.executed_qty,
            "cumQuote": "1.23",
        }

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:
        return {}

    def cancel_all_open_algo_orders(self, symbol: str) -> Mapping[str, Any]:
        return {}


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "sync.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO setup_events
               (event_id, timestamp, symbol, setup_type, payload, received_at)
               VALUES ('evt-1','2026-04-18T00:00:00+00:00','BTCUSDT-PERP','active','{}',
                       datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO execution_tickets
               (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
               VALUES ('ticket-1','evt-1','accepted',0,?,'2026-04-18T00:00:00+00:00')""",
            (_ticket().model_dump_json(),),
        )
        conn.execute(
            """INSERT INTO order_idempotency
               (client_order_id, ticket_id, order_role, symbol, side, quantity, price, status)
               VALUES ('DAENBTICKET1','ticket-1','entry','BTCUSDT-PERP','buy',0.01,100,'submitted')"""
        )
        conn.execute(
            """INSERT INTO orders
               (order_id, ticket_id, exchange_order_id, side, type, symbol, price,
                quantity, status, submitted_at)
               VALUES ('DAENBTICKET1','ticket-1','ex-1','buy','LIMIT','BTCUSDT',100,
                       0.01,'new',datetime('now'))"""
        )
        conn.commit()
    return db


def _ticket() -> ExecutionTicket:
    return ExecutionTicket(
        ticket_id="ticket-1",
        source_event_id="evt-1",
        symbol="BTCUSDT-PERP",
        direction="long",
        regime="vol_breakout_card",
        ranking_score=8.0,
        shadow_mode=False,
        gate="gate2",
        entry_price=100.0,
        stop_price=99.0,
        take_profit_price=102.0,
        quantity=0.01,
        notional_usd=1.0,
        leverage=2.0,
        risk_usd=0.01,
        orders=[
            PlannedOrder(
                role="entry",
                side="buy",
                type="limit",
                symbol="BTCUSDT-PERP",
                price=100.0,
                quantity=0.01,
            ),
            PlannedOrder(
                role="stop",
                side="sell",
                type="stop_market",
                symbol="BTCUSDT-PERP",
                price=99.0,
                quantity=0.01,
                reduce_only=True,
            ),
            PlannedOrder(
                role="take_profit",
                side="sell",
                type="limit",
                symbol="BTCUSDT-PERP",
                price=102.0,
                quantity=0.01,
                reduce_only=True,
            ),
        ],
        created_at="2026-04-18T00:00:00+00:00",
    )


def test_sync_symbol_updates_filled_order(ready_db: Path) -> None:
    client = FakeClient(status="FILLED")
    sync = LiveOrderStatusSync(client=client, db_path=ready_db)

    results = sync.sync_symbol("BTCUSDT-PERP")

    assert len(results) == 1
    assert results[0].local_status == "filled"
    with get_db(ready_db) as conn:
        idem = conn.execute(
            "SELECT status FROM order_idempotency WHERE client_order_id='DAENBTICKET1'"
        ).fetchone()
        order = conn.execute("SELECT status, fill_quantity, fill_price FROM orders").fetchone()
        position = conn.execute(
            "SELECT status, filled_quantity, entry_price FROM positions"
        ).fetchone()
    assert idem["status"] == "filled"
    assert order["status"] == "filled"
    assert order["fill_quantity"] == 0.01
    assert order["fill_price"] == 123.0
    assert position["status"] == "open"
    assert position["filled_quantity"] == 0.01
    assert position["entry_price"] == 123.0


def test_sync_symbol_keeps_partially_filled_as_acknowledged(ready_db: Path) -> None:
    sync = LiveOrderStatusSync(
        client=FakeClient(status="PARTIALLY_FILLED", executed_qty="0.005"),
        db_path=ready_db,
    )

    result = sync.sync_symbol("BTCUSDT-PERP")[0]

    assert result.local_status == "acknowledged"
    with get_db(ready_db) as conn:
        row = conn.execute("SELECT status FROM orders").fetchone()
        position = conn.execute("SELECT status, filled_quantity FROM positions").fetchone()
    assert row["status"] == "partially_filled"
    assert position["status"] == "partial"
    assert position["filled_quantity"] == 0.005


def test_sync_heals_reserved_row_when_exchange_confirms_filled(
    ready_db: Path,
) -> None:
    """A reserved row whose exchange order is actually FILLED must transition
    to 'filled' on next sync (Task 8 — heal stuck reservations)."""
    with get_db(ready_db) as conn:
        conn.execute(
            "UPDATE order_idempotency SET status='reserved' WHERE client_order_id='DAENBTICKET1'"
        )
        conn.commit()

    sync = LiveOrderStatusSync(client=FakeClient(status="FILLED"), db_path=ready_db)
    results = sync.sync_symbol("BTCUSDT-PERP")

    assert len(results) == 1
    assert results[0].local_status == "filled"
    with get_db(ready_db) as conn:
        idem = conn.execute(
            "SELECT status FROM order_idempotency WHERE client_order_id='DAENBTICKET1'"
        ).fetchone()
    assert idem["status"] == "filled"


class NotFoundClient(FakeClient):
    """Simulates Binance returning 'order does not exist' for unknown cids."""

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:  # type: ignore[override]
        from execution.binance_testnet_broker import LiveBrokerError

        raise LiveBrokerError(
            'binance_http_error status=400 path=/fapi/v1/order body={"code":-2013,"msg":"Order does not exist."}'
        )

    def query_algo_order(self, symbol: str, client_algo_id: str) -> Mapping[str, Any]:  # type: ignore[override]
        return self.query_order(symbol, client_algo_id)


def test_sync_marks_reserved_row_rejected_when_exchange_has_no_order(
    ready_db: Path,
) -> None:
    """Submit timed out before reaching the exchange → exchange returns -2013
    on query → row must transition reserved → rejected so retries unblock."""
    with get_db(ready_db) as conn:
        conn.execute(
            "UPDATE order_idempotency SET status='reserved' WHERE client_order_id='DAENBTICKET1'"
        )
        conn.commit()

    sync = LiveOrderStatusSync(client=NotFoundClient(), db_path=ready_db)
    results = sync.sync_symbol("BTCUSDT-PERP")

    assert len(results) == 1
    assert results[0].local_status == "rejected"
    assert results[0].exchange_status == "NOT_FOUND"
    with get_db(ready_db) as conn:
        idem = conn.execute(
            "SELECT status FROM order_idempotency WHERE client_order_id='DAENBTICKET1'"
        ).fetchone()
    assert idem["status"] == "rejected"


class TransientErrorClient(FakeClient):
    """Returns a non-not-found broker error to test that sync re-raises it."""

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:  # type: ignore[override]
        from execution.binance_testnet_broker import LiveBrokerError

        raise LiveBrokerError(
            "binance_http_error status=500 path=/fapi/v1/order body=internal error"
        )


def test_sync_reraises_non_not_found_broker_error(ready_db: Path) -> None:
    """A 5xx must NOT be silently turned into 'rejected' — only -2013 is safe."""
    from execution.binance_testnet_broker import LiveBrokerError

    sync = LiveOrderStatusSync(client=TransientErrorClient(), db_path=ready_db)

    with pytest.raises(LiveBrokerError, match="status=500"):
        sync.sync_symbol("BTCUSDT-PERP")

    # Row remains in its original state — fail-closed.
    with get_db(ready_db) as conn:
        idem = conn.execute(
            "SELECT status FROM order_idempotency WHERE client_order_id='DAENBTICKET1'"
        ).fetchone()
    assert idem["status"] == "submitted"


def test_sync_filled_stop_closes_live_position(ready_db: Path) -> None:
    with get_db(ready_db) as conn:
        conn.execute(
            """UPDATE order_idempotency
                  SET status='filled'
                WHERE client_order_id='DAENBTICKET1'"""
        )
        conn.execute(
            """INSERT INTO order_idempotency
               (client_order_id, ticket_id, order_role, symbol, side, quantity, price, status)
               VALUES ('DASTSTICKET1','ticket-1','stop','BTCUSDT-PERP','sell',0.01,99,'submitted')"""
        )
        conn.execute(
            """INSERT INTO orders
               (order_id, ticket_id, exchange_order_id, side, type, symbol, price,
                quantity, status, submitted_at)
               VALUES ('DASTSTICKET1','ticket-1','ex-stop','sell','STOP_MARKET','BTCUSDT',99,
                       0.01,'new',datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO positions
               (position_id, ticket_id, symbol, direction, status, entry_price,
                quantity, filled_quantity, stop_price, take_profit_price, opened_at,
                fees_usd, shadow_mode)
               VALUES ('pos-1','ticket-1','BTCUSDT-PERP','long','open',100,
                       0.01,0.01,99,102,datetime('now'),0,0)"""
        )
        conn.commit()
    client = FakeClient(status="FILLED", executed_qty="0.01")

    LiveOrderStatusSync(client=client, db_path=ready_db).sync_symbol("BTCUSDT-PERP")

    with get_db(ready_db) as conn:
        position = conn.execute(
            "SELECT status, exit_reason, exit_price, net_pnl_usd FROM positions WHERE position_id='pos-1'"
        ).fetchone()
        ticket = conn.execute(
            "SELECT status FROM execution_tickets WHERE ticket_id='ticket-1'"
        ).fetchone()
    assert position["status"] == "closed"
    assert position["exit_reason"] == "stop_loss"
    assert position["exit_price"] == 123.0
    assert position["net_pnl_usd"] == pytest.approx(0.23)
    assert ticket["status"] == "closed"
