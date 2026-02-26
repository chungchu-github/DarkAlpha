from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.data.datastore import FundingRatePoint, SymbolSnapshot
from dark_alpha_phase_one.service import derivatives_are_fresh


def _snapshot(funding_ts: datetime | None, oi_ts: datetime | None) -> SymbolSnapshot:
    return SymbolSnapshot(
        symbol="BTCUSDT",
        price=100.0,
        klines_1m=[Candle(open=1, high=2, low=1, close=1.5)],
        last_price_ts=datetime.now(tz=timezone.utc),
        last_kline_close_ts=datetime.now(tz=timezone.utc),
        data_source_mode="ws",
        last_funding_rate=0.0001,
        next_funding_time_ms=1700000000000,
        mark_price=100.1,
        funding_rate_history=[FundingRatePoint(funding_rate=0.0001, funding_time=1700000000000)],
        open_interest=1234.0,
        open_interest_ts=oi_ts,
        funding_ts=funding_ts,
        open_interest_series=[(datetime.now(tz=timezone.utc), 1234.0)],
    )


def test_derivatives_fresh_true_for_recent_data() -> None:
    now = datetime.now(tz=timezone.utc)
    snap = _snapshot(funding_ts=now - timedelta(seconds=30), oi_ts=now - timedelta(seconds=5))
    assert derivatives_are_fresh(snap, funding_stale_seconds=180, oi_stale_seconds=30)


def test_derivatives_fresh_false_for_stale_data() -> None:
    now = datetime.now(tz=timezone.utc)
    snap = _snapshot(funding_ts=now - timedelta(seconds=300), oi_ts=now - timedelta(seconds=50))
    assert derivatives_are_fresh(snap, funding_stale_seconds=180, oi_stale_seconds=30) is False
