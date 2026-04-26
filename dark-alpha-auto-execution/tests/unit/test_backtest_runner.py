"""Tests for the basic historical backtest runner."""

from datetime import UTC, datetime, timedelta

from backtest.runner import (
    BacktestSummary,
    HistoricalCandle,
    HistoricalPriceSource,
    run_backtest,
    summarize,
)
from signal_adapter.schemas import InvalidationInfo, SetupEvent, TriggerInfo
from storage.db import get_db
from storage.signal_journal import record_signal


class FakeHistoricalSource(HistoricalPriceSource):
    def __init__(self, candles: dict[str, list[HistoricalCandle]]) -> None:
        self._candles = candles

    def candles(self, symbol: str, start: datetime, end: datetime) -> list[HistoricalCandle]:
        return [
            candle
            for candle in self._candles.get(symbol, [])
            if start <= candle.ts <= end
        ]


def _event(event_id: str, ts: datetime, direction: str = "long") -> SetupEvent:
    stop = 99.0 if direction == "long" else 101.0
    tp = 102.0 if direction == "long" else 98.0
    return SetupEvent(
        event_id=event_id,
        timestamp=ts.isoformat(),
        symbol="BTCUSDT-PERP",
        setup_type="active",
        direction=direction,
        regime="vol_breakout_card",
        today_decision="test",
        ranking_score=8.0,
        trigger=TriggerInfo(condition="entry", price_level=100.0, timeframe="15m"),
        invalidation=InvalidationInfo(condition="stop", price_level=stop),
        metadata={
            "ttl_minutes": 5,
            "take_profit_price": tp,
            "data_health": {"status": "fresh", "reason": "ok"},
        },
    )


def _persist(event: SetupEvent, db_path) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO setup_events
                (event_id, timestamp, symbol, setup_type, payload, received_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (event.event_id, event.timestamp, event.symbol, event.setup_type, event.model_dump_json()),
        )
        conn.commit()
    record_signal(event, db_path=db_path)


def test_backtest_long_take_profit(db_path) -> None:
    ts = datetime(2026, 4, 18, 0, 0, tzinfo=UTC)
    _persist(_event("bt-1", ts), db_path)
    source = FakeHistoricalSource(
        {
            "BTCUSDT-PERP": [
                HistoricalCandle(ts + timedelta(minutes=1), high=100.5, low=99.8, close=100.1),
                HistoricalCandle(ts + timedelta(minutes=2), high=102.2, low=100.0, close=102.0),
            ]
        }
    )

    trades = run_backtest(source, db_path=db_path)

    assert len(trades) == 1
    assert trades[0].status == "closed"
    assert trades[0].exit_reason == "take_profit"
    assert trades[0].r_multiple == 2.0
    summary = summarize(trades)
    assert isinstance(summary, BacktestSummary)
    assert summary.entered == 1
    assert summary.wins == 1


def test_backtest_expires_when_entry_not_touched(db_path) -> None:
    ts = datetime(2026, 4, 18, 0, 0, tzinfo=UTC)
    _persist(_event("bt-expire", ts), db_path)
    source = FakeHistoricalSource(
        {
            "BTCUSDT-PERP": [
                HistoricalCandle(ts + timedelta(minutes=1), high=101.0, low=100.5, close=100.8),
                HistoricalCandle(ts + timedelta(minutes=6), high=100.2, low=99.8, close=100.0),
            ]
        }
    )

    trades = run_backtest(source, db_path=db_path)

    assert trades[0].status == "expired"
    assert trades[0].entry_price is None


def test_backtest_short_stop_loss(db_path) -> None:
    ts = datetime(2026, 4, 18, 0, 0, tzinfo=UTC)
    _persist(_event("bt-short", ts, direction="short"), db_path)
    source = FakeHistoricalSource(
        {
            "BTCUSDT-PERP": [
                HistoricalCandle(ts + timedelta(minutes=1), high=100.2, low=99.5, close=99.8),
                HistoricalCandle(ts + timedelta(minutes=2), high=101.2, low=99.0, close=101.0),
            ]
        }
    )

    trades = run_backtest(source, db_path=db_path)

    assert trades[0].status == "closed"
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].r_multiple == -1.0
