from __future__ import annotations

from datetime import datetime, timezone

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.strategies.vol_breakout import VolBreakoutStrategy


def _context(return_5m: float, atr_15m: float, baseline: float) -> SignalContext:
    return SignalContext(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        price=100.0,
        klines_1m=[Candle(open=100, high=101, low=99, close=100)],
        return_5m=return_5m,
        atr_15m=atr_15m,
        atr_15m_baseline=baseline,
    )


def test_vol_breakout_generates_long_card_on_return_trigger() -> None:
    strategy = VolBreakoutStrategy(
        return_threshold=0.012,
        atr_spike_multiplier=2.0,
        leverage_suggest=50,
        max_risk_usdt=10,
        ttl_minutes=15,
    )

    card = strategy.generate(_context(return_5m=0.02, atr_15m=1.0, baseline=1.0))

    assert card is not None
    assert card.strategy == "vol_breakout_card"
    assert card.side == "LONG"


def test_vol_breakout_returns_none_without_trigger() -> None:
    strategy = VolBreakoutStrategy(
        return_threshold=0.012,
        atr_spike_multiplier=2.0,
        leverage_suggest=50,
        max_risk_usdt=10,
        ttl_minutes=15,
    )

    card = strategy.generate(_context(return_5m=0.001, atr_15m=1.5, baseline=1.0))

    assert card is None
