from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.data.datastore import FundingRatePoint, SymbolSnapshot
from dark_alpha_phase_one.data.source_manager import SourceManager
from dark_alpha_phase_one.service import derivatives_are_fresh


def _snapshot(now: datetime) -> SymbolSnapshot:
    return SymbolSnapshot(
        symbol="BTCUSDT",
        price=100.0,
        klines_1m=[Candle(open=1, high=2, low=1, close=1.5)],
        last_price_ts=now,
        last_kline_close_ts=now,
        last_kline_recv_ts=now,
        data_source_mode="ws",
        last_funding_rate=0.0001,
        next_funding_time_ms=1700000000000,
        mark_price=100.1,
        funding_rate_history=[FundingRatePoint(funding_rate=0.0001, funding_time=1700000000000)],
        open_interest=1234.0,
        open_interest_ts=now - timedelta(seconds=10),
        funding_ts=now - timedelta(seconds=20),
        open_interest_series=[(now, 1234.0)],
    )


def test_health_age_and_derivatives_gating_are_consistent() -> None:
    now = datetime.now(tz=timezone.utc)
    now_ms = SourceManager.dt_to_ms(now)
    assert now_ms is not None
    snap = _snapshot(now)

    funding_raw_age_ms = SourceManager.raw_age_ms(now_ms, SourceManager.dt_to_ms(snap.funding_ts))
    oi_raw_age_ms = SourceManager.raw_age_ms(now_ms, SourceManager.dt_to_ms(snap.open_interest_ts))

    assert funding_raw_age_ms is not None and funding_raw_age_ms < 180_000
    assert oi_raw_age_ms is not None and oi_raw_age_ms < 30_000
    assert derivatives_are_fresh(
        snap,
        now_ms_corrected=now_ms,
        funding_stale_ms=180_000,
        oi_stale_ms=30_000,
    )


def test_derivatives_gating_turns_false_when_health_raw_age_exceeds_threshold() -> None:
    now = datetime.now(tz=timezone.utc)
    now_ms = SourceManager.dt_to_ms(now)
    assert now_ms is not None
    snap = _snapshot(now)
    snap = replace(snap, open_interest_ts=now - timedelta(seconds=45))

    oi_raw_age_ms = SourceManager.raw_age_ms(now_ms, SourceManager.dt_to_ms(snap.open_interest_ts))
    assert oi_raw_age_ms is not None and oi_raw_age_ms > 30_000
    assert (
        derivatives_are_fresh(
            snap,
            now_ms_corrected=now_ms,
            funding_stale_ms=180_000,
            oi_stale_ms=30_000,
        )
        is False
    )
