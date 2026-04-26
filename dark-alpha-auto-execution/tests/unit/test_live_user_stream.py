"""Tests for Binance user-data stream ingestion."""

from pathlib import Path

import pytest

from execution.live_safety import client_order_id
from execution.live_user_stream import LiveUserStreamIngestor
from storage.db import get_db, init_db
from strategy.schemas import ExecutionTicket, PlannedOrder


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "stream.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    ticket = _ticket()
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
            (ticket.model_dump_json(),),
        )
        for order in ticket.orders:
            cid = client_order_id(ticket, order)
            conn.execute(
                """INSERT INTO order_idempotency
                   (client_order_id, ticket_id, order_role, symbol, side, quantity, price, status)
                   VALUES (?, 'ticket-1', ?, 'BTCUSDT-PERP', ?, 0.01, ?, 'submitted')""",
                (cid, order.role, order.side, order.price),
            )
            conn.execute(
                """INSERT INTO orders
                   (order_id, ticket_id, exchange_order_id, side, type, symbol, price,
                    quantity, status, submitted_at, fill_quantity)
                   VALUES (?, 'ticket-1', ?, ?, ?, 'BTCUSDT', ?, 0.01, 'new',
                           datetime('now'), 0)""",
                (cid, f"ex-{cid}", order.side, order.type, order.price),
            )
        conn.commit()
    return db


def _ticket() -> ExecutionTicket:
    return ExecutionTicket(
        ticket_id="ticket-1",
        source_event_id="evt-1",
        symbol="BTCUSDT-PERP",
        direction="long",
        regime="gate6_manual_canary",
        ranking_score=9.0,
        shadow_mode=False,
        gate="gate6",
        entry_price=100.0,
        stop_price=99.0,
        take_profit_price=102.0,
        quantity=0.01,
        notional_usd=1.0,
        leverage=1.0,
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


def _order_event(
    *,
    cid: str,
    status: str,
    execution_type: str = "TRADE",
    cumulative: str = "0.01",
    last: str = "0.01",
    avg: str = "100.5",
    trade_id: str = "123",
) -> dict[str, object]:
    return {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1_774_000_000_000,
        "o": {
            "s": "BTCUSDT",
            "c": cid,
            "S": "BUY",
            "o": "LIMIT",
            "q": "0.01",
            "p": "100",
            "ap": avg,
            "x": execution_type,
            "X": status,
            "l": last,
            "z": cumulative,
            "L": avg,
            "i": 987,
            "t": trade_id,
        },
    }


def test_user_stream_entry_fill_opens_position(ready_db: Path) -> None:
    ticket = _ticket()
    cid = client_order_id(ticket, ticket.orders[0])

    result = LiveUserStreamIngestor(db_path=ready_db).process_event(
        _order_event(cid=cid, status="FILLED", avg="100.5")
    )

    assert result is not None
    assert result.action == "known_order:entry"
    with get_db(ready_db) as conn:
        order = conn.execute(
            "SELECT status, fill_quantity, fill_price FROM orders WHERE order_id=?", (cid,)
        ).fetchone()
        position = conn.execute(
            "SELECT status, filled_quantity, entry_price FROM positions"
        ).fetchone()
        event_count = conn.execute("SELECT COUNT(*) AS n FROM live_stream_events").fetchone()
    assert order["status"] == "filled"
    assert order["fill_quantity"] == 0.01
    assert order["fill_price"] == 100.5
    assert position["status"] == "open"
    assert position["filled_quantity"] == 0.01
    assert event_count["n"] == 1


def test_user_stream_duplicate_event_is_ignored(ready_db: Path) -> None:
    ticket = _ticket()
    cid = client_order_id(ticket, ticket.orders[0])
    ingestor = LiveUserStreamIngestor(db_path=ready_db)
    payload = _order_event(cid=cid, status="FILLED", avg="100.5")

    first = ingestor.process_event(payload)
    second = ingestor.process_event(payload)

    assert first is not None
    assert second is not None
    assert second.action == "duplicate_ignored"
    with get_db(ready_db) as conn:
        event_count = conn.execute("SELECT COUNT(*) AS n FROM live_stream_events").fetchone()
        position = conn.execute("SELECT filled_quantity FROM positions").fetchone()
    assert event_count["n"] == 1
    assert position["filled_quantity"] == 0.01


def test_user_stream_stop_fill_closes_position(ready_db: Path) -> None:
    ticket = _ticket()
    entry_cid = client_order_id(ticket, ticket.orders[0])
    stop_cid = client_order_id(ticket, ticket.orders[1])
    ingestor = LiveUserStreamIngestor(db_path=ready_db)
    ingestor.process_event(_order_event(cid=entry_cid, status="FILLED", avg="100.0", trade_id="1"))

    result = ingestor.process_event(
        _order_event(cid=stop_cid, status="FILLED", avg="99.0", trade_id="2")
    )

    assert result is not None
    assert result.action == "known_order:stop"
    with get_db(ready_db) as conn:
        position = conn.execute("SELECT status, exit_reason, exit_price FROM positions").fetchone()
    assert position["status"] == "closed"
    assert position["exit_reason"] == "stop_loss"
    assert position["exit_price"] == 99.0


def test_user_stream_emergency_close_closes_active_symbol_position(ready_db: Path) -> None:
    ticket = _ticket()
    entry_cid = client_order_id(ticket, ticket.orders[0])
    ingestor = LiveUserStreamIngestor(db_path=ready_db)
    ingestor.process_event(_order_event(cid=entry_cid, status="FILLED", avg="100.0", trade_id="1"))

    result = ingestor.process_event(
        _order_event(
            cid="DACLOSEBTCUSDT123",
            status="FILLED",
            cumulative="0.01",
            last="0.01",
            avg="100.2",
            trade_id="flatten-1",
        )
    )

    assert result is not None
    assert result.action == "emergency_close"
    with get_db(ready_db) as conn:
        position = conn.execute(
            "SELECT status, exit_reason, exit_price, filled_quantity FROM positions"
        ).fetchone()
        flatten_order = conn.execute(
            "SELECT status, fill_quantity FROM orders WHERE order_id='DACLOSEBTCUSDT123'"
        ).fetchone()
    assert position["status"] == "closed"
    assert position["exit_reason"] == "manual"
    assert position["exit_price"] == 100.2
    assert position["filled_quantity"] == 0
    assert flatten_order["status"] == "filled"
    assert flatten_order["fill_quantity"] == 0.01


def test_user_stream_emergency_close_partial_fills_are_delta_applied(ready_db: Path) -> None:
    ticket = _ticket()
    entry_cid = client_order_id(ticket, ticket.orders[0])
    ingestor = LiveUserStreamIngestor(db_path=ready_db)
    ingestor.process_event(_order_event(cid=entry_cid, status="FILLED", avg="100.0", trade_id="1"))

    first = ingestor.process_event(
        _order_event(
            cid="DACLOSEBTCUSDT456",
            status="PARTIALLY_FILLED",
            cumulative="0.004",
            last="0.004",
            avg="100.2",
            trade_id="flatten-1",
        )
    )
    second = ingestor.process_event(
        _order_event(
            cid="DACLOSEBTCUSDT456",
            status="FILLED",
            cumulative="0.01",
            last="0.006",
            avg="100.3",
            trade_id="flatten-2",
        )
    )

    assert first is not None
    assert first.fill_delta == 0.004
    assert second is not None
    assert second.fill_delta == pytest.approx(0.006)
    with get_db(ready_db) as conn:
        position = conn.execute("SELECT status, filled_quantity FROM positions").fetchone()
        flatten_order = conn.execute(
            "SELECT status, fill_quantity FROM orders WHERE order_id='DACLOSEBTCUSDT456'"
        ).fetchone()
    assert position["status"] == "closed"
    assert position["filled_quantity"] == 0
    assert flatten_order["status"] == "filled"
    assert flatten_order["fill_quantity"] == 0.01
