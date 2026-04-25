"""Unit tests for execution.evaluator."""

from datetime import UTC, datetime
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
