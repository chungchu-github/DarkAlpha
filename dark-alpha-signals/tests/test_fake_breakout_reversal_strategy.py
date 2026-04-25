from __future__ import annotations

from datetime import datetime, timezone

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.strategies.fake_breakout_reversal import FakeBreakoutReversalStrategy


def _base_ctx(last: Candle) -> SignalContext:
    candles = [Candle(open=100, high=101, low=99, close=100) for _ in range(20)] + [last]
    return SignalContext(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        price=100.0,
        klines_1m=candles,
        return_5m=0.01,
        atr_15m=1.0,
        atr_15m_baseline=0.8,
        funding_rate=0.0002,
        open_interest=1000.0,
        mark_price=100.1,
        open_interest_zscore_15m=1.2,
        open_interest_delta_15m=0.05,
        last_kline_close_ts=datetime(2026, 2, 25, 11, 59, 30, tzinfo=timezone.utc),
    )


def _strategy() -> FakeBreakoutReversalStrategy:
    return FakeBreakoutReversalStrategy(
        sweep_pct=0.001,
        wick_body_ratio=2.0,
        stop_buffer_atr=0.3,
        min_atr_pct=0.001,
        leverage_suggest=50,
        max_risk_usdt=10,
        ttl_minutes=5,
    )


def test_sweep_high_reclaim_generates_short() -> None:
    # prev high ~101; latest high sweeps above, closes back below
    last = Candle(open=100.0, high=102.0, low=99.8, close=100.5)
    card = _strategy().generate(_base_ctx(last))
    assert card is not None
    assert card.side == "SHORT"


def test_sweep_low_reclaim_generates_long() -> None:
    # prev low ~99; latest low sweeps below, closes back above
    last = Candle(open=99.7, high=100.0, low=98.5, close=99.6)
    ctx = _base_ctx(last)
    card = _strategy().generate(ctx)
    assert card is not None
    assert card.side == "LONG"


def test_wick_body_ratio_not_enough_returns_none() -> None:
    last = Candle(open=100.0, high=101.2, low=99.8, close=101.1)
    card = _strategy().generate(_base_ctx(last))
    assert card is None


def test_reclaim_not_satisfied_returns_none() -> None:
    # swept high but close still above previous high
    last = Candle(open=100.8, high=102.0, low=100.7, close=101.2)
    card = _strategy().generate(_base_ctx(last))
    assert card is None
