"""Unit tests for execution.position_manager."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from execution.paper_broker import Fill, PaperBroker
from execution.position_manager import PositionManager
from storage.db import get_db, init_db
from strategy.schemas import ExecutionTicket, PlannedOrder, Rejection


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "pm.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    # Seed a setup_event so FK constraints on execution_tickets pass
    with get_db(db) as conn:
        for eid in ("evt-1", "evt-x"):
            conn.execute(
                """INSERT INTO setup_events
                   (event_id, timestamp, symbol, setup_type, payload, received_at)
                   VALUES (?, '2026-04-18T00:00:00+00:00', 'BTCUSDT-PERP', 'active',
                           '{}', datetime('now'))""",
                (eid,),
            )
        conn.commit()
    return db


def _ticket() -> ExecutionTicket:
    return ExecutionTicket(
        ticket_id="tkt-1",
        source_event_id="evt-1",
        symbol="BTCUSDT-PERP",
        direction="long",
        regime="x",
        ranking_score=8.0,
        shadow_mode=True,
        gate="gate1",
        entry_price=100.0,
        stop_price=99.0,
        take_profit_price=102.0,
        quantity=1.0,
        notional_usd=100.0,
        leverage=1.0,
        risk_usd=1.0,
        orders=[
            PlannedOrder(role="entry", side="buy", type="limit", symbol="BTCUSDT-PERP",
                         price=100.0, quantity=1.0),
            PlannedOrder(role="stop", side="sell", type="stop_market",
                         symbol="BTCUSDT-PERP", price=99.0, quantity=1.0, reduce_only=True),
            PlannedOrder(role="take_profit", side="sell", type="limit",
                         symbol="BTCUSDT-PERP", price=102.0, quantity=1.0, reduce_only=True),
        ],
        created_at=datetime.now(tz=UTC).isoformat(),
    )


def test_persist_ticket_and_open_position(ready_db: Path) -> None:
    pm = PositionManager(db_path=ready_db)
    ticket = _ticket()
    pm.persist_ticket(ticket)
    fill = PaperBroker().simulate_entry(ticket)
    pos_id = pm.open_position(ticket, fill)

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status, entry_price, symbol FROM positions WHERE position_id=?",
            (pos_id,),
        ).fetchone()
    assert row["status"] == "open"
    assert row["symbol"] == "BTCUSDT-PERP"

    with get_db(ready_db) as conn:
        trow = conn.execute(
            "SELECT status FROM execution_tickets WHERE ticket_id=?", (ticket.ticket_id,)
        ).fetchone()
    assert trow["status"] == "filled"


def test_create_pending_and_fill_position(ready_db: Path) -> None:
    pm = PositionManager(db_path=ready_db)
    ticket = _ticket()
    pm.persist_ticket(ticket, status="accepted")
    pos_id = pm.create_pending_position(ticket)
    fill = PaperBroker(slippage_bps=0).simulate_entry(ticket)
    pm.fill_pending_position(pos_id, ticket, fill)

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status, filled_quantity FROM positions WHERE position_id=?",
            (pos_id,),
        ).fetchone()
        trow = conn.execute(
            "SELECT status FROM execution_tickets WHERE ticket_id=?",
            (ticket.ticket_id,),
        ).fetchone()

    assert row["status"] == "open"
    assert row["filled_quantity"] == pytest.approx(1.0)
    assert trow["status"] == "filled"


def test_expire_pending_position(ready_db: Path) -> None:
    pm = PositionManager(db_path=ready_db)
    ticket = _ticket()
    pm.persist_ticket(ticket, status="accepted")
    pos_id = pm.create_pending_position(ticket)
    pm.expire_pending_position(pos_id, ticket.ticket_id)

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status, exit_reason FROM positions WHERE position_id=?",
            (pos_id,),
        ).fetchone()
        trow = conn.execute(
            "SELECT status FROM execution_tickets WHERE ticket_id=?",
            (ticket.ticket_id,),
        ).fetchone()

    assert row["status"] == "cancelled"
    assert row["exit_reason"] == "ttl_expired"
    assert trow["status"] == "expired"


def test_close_position_profit(ready_db: Path) -> None:
    pm = PositionManager(db_path=ready_db)
    ticket = _ticket()
    pm.persist_ticket(ticket)
    entry_fill = PaperBroker(slippage_bps=0).simulate_entry(ticket)
    pos_id = pm.open_position(ticket, entry_fill)

    exit_fill = Fill(
        order_role="take_profit", side="sell", symbol="BTCUSDT-PERP",
        price=102.0, quantity=1.0, fee_usd=0.04, reduce_only=True,
    )
    pnl = pm.close_position(pos_id, exit_fill, reason="take_profit")
    assert pnl["gross_pnl_usd"] == pytest.approx(2.0)
    assert pnl["net_pnl_usd"] < 2.0  # fees subtracted


def test_close_position_loss_short(ready_db: Path) -> None:
    pm = PositionManager(db_path=ready_db)
    ticket = _ticket().model_copy(update={"direction": "short"})
    pm.persist_ticket(ticket)
    entry_fill = Fill(
        order_role="entry", side="sell", symbol="BTCUSDT-PERP",
        price=100.0, quantity=1.0, fee_usd=0.02, reduce_only=False,
    )
    pos_id = pm.open_position(ticket, entry_fill)
    exit_fill = Fill(
        order_role="stop", side="buy", symbol="BTCUSDT-PERP",
        price=101.0, quantity=1.0, fee_usd=0.04, reduce_only=True,
    )
    pnl = pm.close_position(pos_id, exit_fill, reason="stop_loss")
    assert pnl["gross_pnl_usd"] == pytest.approx(-1.0)


def test_close_unknown_position_raises(ready_db: Path) -> None:
    pm = PositionManager(db_path=ready_db)
    exit_fill = Fill(
        order_role="stop", side="sell", symbol="X", price=1, quantity=1, fee_usd=0,
        reduce_only=True,
    )
    with pytest.raises(ValueError):
        pm.close_position("nope", exit_fill, reason="manual")


def test_persist_rejection_creates_row(ready_db: Path) -> None:
    pm = PositionManager(db_path=ready_db)
    rej = Rejection(source_event_id="evt-x", stage="validator", reason="low_ranking_score")
    pm.persist_rejection(rej)
    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status, reject_reason FROM execution_tickets WHERE source_event_id=?",
            ("evt-x",),
        ).fetchone()
    assert row["status"] == "rejected"
    assert "validator" in row["reject_reason"]


def test_equity_snapshot_and_current_equity(ready_db: Path) -> None:
    pm = PositionManager(db_path=ready_db)
    pm.snapshot_equity(10_000.0, mode="shadow", gate="gate1", realized=0, unrealized=0)

    # With zero closed positions, current_equity == starting_equity
    assert pm.current_equity(10_000.0) == pytest.approx(10_000.0)

    # Open + close one profitable position
    ticket = _ticket()
    pm.persist_ticket(ticket)
    entry_fill = Fill(order_role="entry", side="buy", symbol="BTCUSDT-PERP",
                      price=100.0, quantity=1.0, fee_usd=0.02, reduce_only=False)
    pid = pm.open_position(ticket, entry_fill)
    exit_fill = Fill(order_role="take_profit", side="sell", symbol="BTCUSDT-PERP",
                     price=105.0, quantity=1.0, fee_usd=0.03, reduce_only=True)
    pm.close_position(pid, exit_fill, reason="take_profit")
    assert pm.current_equity(10_000.0) > 10_000.0
