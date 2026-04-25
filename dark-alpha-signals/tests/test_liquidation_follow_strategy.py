from __future__ import annotations

from datetime import datetime, timezone

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.strategies.liquidation_follow import LiquidationFollowStrategy


def _ctx(oi_delta: float | None, ret: float, funding: float) -> SignalContext:
    return SignalContext(
        symbol="ETHUSDT",
        timestamp=datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        price=200.0,
        klines_1m=[Candle(open=1, high=2, low=1, close=1.5)],
        return_5m=ret,
        atr_15m=2.0,
        atr_15m_baseline=1.5,
        funding_rate=funding,
        open_interest=2000.0,
        mark_price=200.2,
        open_interest_zscore_15m=2.2,
        open_interest_delta_15m=oi_delta,
        last_kline_close_ts=datetime(2026, 2, 25, 11, 59, tzinfo=timezone.utc),
    )


def test_liquidation_follow_triggers_long() -> None:
    strategy = LiquidationFollowStrategy(oi_delta_pct_threshold=0.10, leverage_suggest=30, max_risk_usdt=10, ttl_minutes=10)
    card = strategy.generate(_ctx(oi_delta=0.15, ret=0.02, funding=0.001))
    assert card is not None
    assert card.side == "LONG"
    assert card.leverage_suggest == 30


def test_liquidation_follow_returns_none_when_not_aligned() -> None:
    strategy = LiquidationFollowStrategy(oi_delta_pct_threshold=0.10, leverage_suggest=30, max_risk_usdt=10, ttl_minutes=10)
    card = strategy.generate(_ctx(oi_delta=0.15, ret=0.02, funding=-0.001))
    assert card is None
