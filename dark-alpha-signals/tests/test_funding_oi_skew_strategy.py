from __future__ import annotations

from datetime import datetime, timezone

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.strategies.funding_oi_skew import FundingOiSkewStrategy


def _ctx(funding: float, oi_z: float | None, ret: float) -> SignalContext:
    return SignalContext(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        price=100.0,
        klines_1m=[Candle(open=1, high=2, low=1, close=1.5)],
        return_5m=ret,
        atr_15m=1.0,
        atr_15m_baseline=0.8,
        funding_rate=funding,
        open_interest=1000.0,
        mark_price=100.2,
        open_interest_zscore_15m=oi_z,
        open_interest_delta_15m=0.12,
        last_kline_close_ts=datetime(2026, 2, 25, 11, 59, tzinfo=timezone.utc),
    )


def test_funding_oi_skew_triggers_short_on_crowded_long() -> None:
    strategy = FundingOiSkewStrategy(0.0005, 2.0, leverage_suggest=35, max_risk_usdt=10, ttl_minutes=12)
    card = strategy.generate(_ctx(funding=0.0008, oi_z=2.5, ret=0.02))
    assert card is not None
    assert card.side == "SHORT"
    assert "contrarian SHORT" in card.rationale


def test_funding_oi_skew_returns_none_when_not_extreme() -> None:
    strategy = FundingOiSkewStrategy(0.0005, 2.0, leverage_suggest=35, max_risk_usdt=10, ttl_minutes=12)
    card = strategy.generate(_ctx(funding=0.0001, oi_z=2.5, ret=0.02))
    assert card is None
