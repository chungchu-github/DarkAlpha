"""Tests for post-signal outcome evaluation."""

from datetime import UTC, datetime, timedelta

from execution.signal_outcome_evaluator import SignalOutcomeEvaluator
from signal_adapter.schemas import InvalidationInfo, SetupEvent, TriggerInfo
from storage.db import get_db
from storage.signal_journal import record_signal


class FakePriceSource:
    def __init__(self, prices: dict[str, float | None]) -> None:
        self._prices = prices

    def last_price(self, symbol: str) -> float | None:
        return self._prices.get(symbol)


def _event(ts: datetime, direction: str = "long") -> SetupEvent:
    stop = 99.0 if direction == "long" else 101.0
    return SetupEvent(
        event_id=f"outcome-{direction}",
        timestamp=ts.isoformat(),
        symbol="BTCUSDT-PERP",
        setup_type="active",
        direction=direction,
        regime="vol_breakout_card",
        today_decision="test",
        ranking_score=8.0,
        trigger=TriggerInfo(condition="entry", price_level=100.0, timeframe="15m"),
        invalidation=InvalidationInfo(condition="stop", price_level=stop),
        metadata={"data_health": {"status": "fresh", "reason": "ok"}},
    )


def _persist_event(event: SetupEvent, db_path) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO setup_events
                (event_id, timestamp, symbol, setup_type, payload, received_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                event.event_id,
                event.timestamp,
                event.symbol,
                event.setup_type,
                event.model_dump_json(),
            ),
        )
        conn.commit()
    record_signal(event, db_path=db_path)


def test_observes_due_long_signal(db_path) -> None:
    now = datetime.now(tz=UTC)
    event = _event(now - timedelta(minutes=6), direction="long")
    _persist_event(event, db_path)

    evaluator = SignalOutcomeEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 101.0}),
        db_path=db_path,
    )
    results = evaluator.tick(now=now)

    assert len(results) == 1
    assert results[0].horizon == "5m"
    assert results[0].status == "observed"

    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT status, return_pct, r_multiple FROM signal_outcomes WHERE event_id=? AND horizon='5m'",
            (event.event_id,),
        ).fetchone()
    assert row["status"] == "observed"
    assert row["return_pct"] == 0.01
    assert row["r_multiple"] == 1.0


def test_observes_due_short_signal(db_path) -> None:
    now = datetime.now(tz=UTC)
    event = _event(now - timedelta(minutes=16), direction="short")
    _persist_event(event, db_path)

    evaluator = SignalOutcomeEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": 99.0}),
        db_path=db_path,
    )
    results = evaluator.tick(now=now)

    assert {result.horizon for result in results} == {"5m", "15m"}

    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT return_pct, r_multiple FROM signal_outcomes WHERE event_id=? AND horizon='15m'",
            (event.event_id,),
        ).fetchone()
    assert row["return_pct"] == 0.01
    assert row["r_multiple"] == 1.0


def test_failed_price_marks_due_outcome_failed(db_path) -> None:
    now = datetime.now(tz=UTC)
    event = _event(now - timedelta(minutes=6), direction="long")
    _persist_event(event, db_path)

    evaluator = SignalOutcomeEvaluator(
        price_source=FakePriceSource({"BTCUSDT-PERP": None}),
        db_path=db_path,
    )
    results = evaluator.tick(now=now)

    assert len(results) == 1
    assert results[0].status == "failed"

    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM signal_outcomes WHERE event_id=? AND horizon='5m'",
            (event.event_id,),
        ).fetchone()
    assert row["status"] == "failed"
