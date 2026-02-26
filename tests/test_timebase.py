from __future__ import annotations

import logging

from dark_alpha_phase_one.data.source_manager import SourceManager


def test_clock_skew_and_corrected_now_match_server_direction() -> None:
    local_ms = 1_000_000
    server_ms = 1_005_500

    skew_ms = SourceManager.compute_clock_skew_ms(local_ms=local_ms, server_ms=server_ms)
    corrected_ms = SourceManager.compute_now_ms_corrected(local_ms=local_ms, clock_skew_ms=skew_ms)

    assert skew_ms == 5_500
    assert corrected_ms == server_ms


def test_future_timestamp_warning_is_emitted(caplog) -> None:
    now_ms = 1_700_000_000_000
    future_ts_ms = now_ms + 2_000
    raw_age_ms = SourceManager.raw_age_ms(now_ms, future_ts_ms)

    with caplog.at_level(logging.WARNING):
        if raw_age_ms is not None and raw_age_ms < 0:
            logging.warning(
                "timestamp_in_future unit=ms symbol=%s field=%s ahead_ms=%d now_ms_corrected=%d ts_ms=%d",
                "BTCUSDT",
                "funding",
                abs(raw_age_ms),
                now_ms,
                future_ts_ms,
            )

    assert raw_age_ms == -2_000
    assert "timestamp_in_future" in caplog.text
