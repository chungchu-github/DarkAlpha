"""Unit tests for execution.evaluator."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from execution.evaluator import PositionEvaluator
from execution.paper_broker import Fill, PaperBroker
from execution.position_manager import PositionManager
from storage.db import get_db, init_db
from strategy.schemas import ExecutionTicket, PlannedOrder


class FakePriceSource:
    def __init__(self, prices: dict[str, float | None]) -> None:
        self._p = prices

    def last_price(self, symbol: str) -> float | None:
        return self._p.get(symbol)


def _build_ticket(direction: str = "long") -> ExecutionTicket:
    if direction == "long":
        entry_side, exit_side = "buy", "sell"
        entry, stop, tp = 100.0, 99.0, 102.0
    else:
        entry_side, exit_side = "sell", "buy"
        entry, stop, tp = 100.0, 101.0, 98.0
    return ExecutionTicket(
        ticket_id=f"t-{direction}", source_event_id=f"e-{direction}",
        symbol="BTCUSDT-PERP", direction=direction,  # type: ignore[arg-type]
        regime="x", ranking_score=8.0, shadow_mode=True, gate="gate1",
        entry_price=entry, stop_price=stop, take_profit_price=tp,
        quantity=1.0, notional_usd=100.0, leverage=1.0, risk_usd=1.0,
        orders=[
            PlannedOrder(role="entry", side=entry_side, type="limit",  # type: ignore[arg-type]
                         symbol="BTCUSDT-PERP", price=entry, quantity=1.0),
            PlannedOrder(role="stop", side=exit_side, type="stop_market",  # type: ignore[arg-type]
                         symbol="BTCUSDT-PERP", price=stop, quantity=1.0, reduce_only=True),
            PlannedOrder(role="take_profit", side=exit_side, type="limit",  # type: ignore[arg-type]
                         symbol="BTCUSDT-PERP", price=tp, quantity=1.0, reduce_only=True),
        ],
        created_at=datetime.now(tz=UTC).isoformat(),
        metadata={"event_metadata": {"ttl_minutes": 15}},
    )


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "eval.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    with get_db(db) as conn:
        for eid in ("e-long", "e-short"):
            conn.execute(
                """INSERT INTO setup_events
                   (event_id, timestamp, symbol, setup_type, payload, received_at)
                   VALUES (?, '2026-04-18T00:00:00+00:00', 'BTCUSDT-PERP', 'active',
                           '{}', datetime('now'))""",
                (eid,),
            )
        conn.commit()
    return db


def _open_position(ready_db: Path, direction: str = "long") -> tuple[str, ExecutionTicket]:
    pm = PositionManager(db_path=ready_db)
    ticket = _build_ticket(direction)
    pm.persist_ticket(ticket)
    fill = Fill(order_role="entry", side="buy" if direction == "long" else "sell",
                symbol="BTCUSDT-PERP", price=100.0, quantity=1.0,
                fee_usd=0.02, reduce_only=False)
    pid = pm.open_position(ticket, fill)
    return pid, ticket


def _pending_position(
    ready_db: Path,
    direction: str = "long",
    created_at: datetime | None = None,
) -> tuple[str, ExecutionTicket]:
    pm = PositionManager(db_path=ready_db)
    ticket = _build_ticket(direction)
    if created_at is not None:
        ticket = ticket.model_copy(update={"created_at": created_at.isoformat()})
    pm.persist_ticket(ticket, status="accepted")
    pid = pm.create_pending_position(ticket)
    return pid, ticket


def test_no_trigger_when_between_levels(ready_db: Path) -> None:
    _open_position(ready_db, "long")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 100.5}),
        db_path=ready_db,
    )
    results = ev.tick()
    assert len(results) == 1 and results[0].triggered is None


def test_long_stop_triggers(ready_db: Path) -> None:
    pid, _ = _open_position(ready_db, "long")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 98.5}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered == "stop_loss"

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status, exit_reason FROM positions WHERE position_id=?", (pid,)
        ).fetchone()
    assert row["status"] == "closed"
    assert row["exit_reason"] == "stop_loss"


def test_long_tp_triggers(ready_db: Path) -> None:
    _open_position(ready_db, "long")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 102.5}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered == "take_profit"


def test_short_stop_triggers(ready_db: Path) -> None:
    _open_position(ready_db, "short")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 101.5}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered == "stop_loss"


def test_short_tp_triggers(ready_db: Path) -> None:
    _open_position(ready_db, "short")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 97.5}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered == "take_profit"


def test_missing_price_keeps_position_open(ready_db: Path) -> None:
    _open_position(ready_db, "long")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": None}),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered is None and results[0].mark_price is None


def test_no_open_positions_is_empty(ready_db: Path) -> None:
    ev = PositionEvaluator(
        price_source=FakePriceSource({}),
        db_path=ready_db,
    )
    assert ev.tick() == []


def test_pending_long_entry_fills_when_touched(ready_db: Path) -> None:
    pid, ticket = _pending_position(ready_db, "long")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 99.9}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered == "entry_fill"

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status, filled_quantity FROM positions WHERE position_id=?",
            (pid,),
        ).fetchone()
        trow = conn.execute(
            "SELECT status FROM execution_tickets WHERE ticket_id=?",
            (ticket.ticket_id,),
        ).fetchone()
    assert row["status"] == "open"
    assert row["filled_quantity"] == pytest.approx(1.0)
    assert trow["status"] == "filled"


def test_pending_short_entry_fills_when_touched(ready_db: Path) -> None:
    _pending_position(ready_db, "short")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 100.1}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered == "entry_fill"


def test_pending_entry_waits_when_not_touched(ready_db: Path) -> None:
    pid, _ = _pending_position(ready_db, "long")
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 100.5}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered is None

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status FROM positions WHERE position_id=?",
            (pid,),
        ).fetchone()
    assert row["status"] == "pending"


def test_pending_entry_expires_after_ttl(ready_db: Path) -> None:
    pid, ticket = _pending_position(
        ready_db,
        "long",
        created_at=datetime.now(tz=UTC) - timedelta(minutes=20),
    )
    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 99.0}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )
    results = ev.tick()
    assert results[0].triggered == "entry_expired"

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status FROM positions WHERE position_id=?",
            (pid,),
        ).fetchone()
        trow = conn.execute(
            "SELECT status FROM execution_tickets WHERE ticket_id=?",
            (ticket.ticket_id,),
        ).fetchone()
    assert row["status"] == "cancelled"
    assert trow["status"] == "expired"


def test_evaluator_ignores_live_positions(ready_db: Path) -> None:
    ticket = _build_ticket("long").model_copy(
        update={"ticket_id": "live-t", "shadow_mode": False, "gate": "gate2"}
    )
    PositionManager(db_path=ready_db).persist_ticket(ticket, status="filled")
    with get_db(ready_db) as conn:
        conn.execute(
            """INSERT INTO positions
               (position_id, ticket_id, symbol, direction, status, entry_price,
                quantity, filled_quantity, stop_price, take_profit_price,
                opened_at, fees_usd, shadow_mode)
               VALUES ('live-pos','live-t','BTCUSDT-PERP','long','open',100,
                       1,1,99,102,datetime('now'),0,0)"""
        )
        conn.commit()

    ev = PositionEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 98.0}),
        broker=PaperBroker(slippage_bps=0),
        db_path=ready_db,
    )

    assert ev.tick() == []
    with get_db(ready_db) as conn:
        row = conn.execute("SELECT status FROM positions WHERE position_id='live-pos'").fetchone()
    assert row["status"] == "open"
