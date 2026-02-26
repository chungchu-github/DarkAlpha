from __future__ import annotations

import logging
import time

from dark_alpha_phase_one.data.source_manager import ClockSync, SourceManager


class SequenceRestClient:
    def __init__(self, sequence: list[int | Exception]) -> None:
        self.sequence = sequence
        self.calls = 0

    def fetch_server_time_ms(self) -> int:
        item = self.sequence[min(self.calls, len(self.sequence) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


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


def test_clock_sync_does_not_reuse_stale_server_ms_on_failure() -> None:
    rest = SequenceRestClient([1_700_000_000_000, RuntimeError("down")])
    clock = ClockSync(
        rest_client=rest,
        max_clock_error_ms=1,
        refresh_sec=60,
        degraded_retry_sec=10,
        refresh_cooldown_ms=30_000,
        degraded_ttl_ms=60_000,
    )

    assert clock.refresh_server_time(force=True)
    assert clock.state.state == "synced"
    assert clock.state.last_server_ms == 1_700_000_000_000

    assert clock.refresh_server_time(force=True) is False
    assert clock.state.state == "degraded"
    assert clock.state.last_server_ms is None
    assert clock.state.clock_skew_ms == 0

    now_ms = clock.now_ms()
    local_ms = int(time.time() * 1000)
    assert abs(now_ms - local_ms) < 1000


def test_clock_sync_recovers_after_success() -> None:
    rest = SequenceRestClient([RuntimeError("down"), 1_800_000_000_000])
    clock = ClockSync(
        rest_client=rest,
        max_clock_error_ms=1000,
        refresh_sec=60,
        degraded_retry_sec=10,
        refresh_cooldown_ms=30_000,
        degraded_ttl_ms=60_000,
    )

    assert clock.refresh_server_time(force=True) is False
    assert clock.state.state == "degraded"

    assert clock.refresh_server_time(force=True)
    assert clock.state.state == "synced"
    assert clock.state.last_server_ms == 1_800_000_000_000

    now_ms = clock.now_ms()
    assert abs(now_ms - 1_800_000_000_000) < 5000


class AdvancingClock:
    def __init__(self, start_ms: int) -> None:
        self.current_ms = start_ms

    def set_ms(self, value: int) -> None:
        self.current_ms = value

    def time(self) -> float:
        return self.current_ms / 1000

    def monotonic(self) -> float:
        return self.current_ms / 1000

    def perf_counter(self) -> float:
        return self.current_ms / 1000


def test_clock_cooldown_limits_force_refresh_calls(monkeypatch) -> None:
    from dark_alpha_phase_one.data import source_manager as sm

    fake_clock = AdvancingClock(start_ms=1_000_000)
    rest = SequenceRestClient([1_000_000, 1_000_000, 1_000_000])
    clock = ClockSync(
        rest_client=rest,
        max_clock_error_ms=1_000,
        refresh_sec=9999,
        degraded_retry_sec=9999,
        refresh_cooldown_ms=30_000,
        degraded_ttl_ms=60_000,
    )

    monkeypatch.setattr(sm.time, "time", fake_clock.time)
    monkeypatch.setattr(sm.time, "monotonic", fake_clock.monotonic)
    monkeypatch.setattr(sm.time, "perf_counter", fake_clock.perf_counter)

    assert clock.refresh_server_time(force=True)
    assert rest.calls == 1

    fake_clock.set_ms(1_020_000)
    _ = clock.now_ms()
    assert rest.calls == 2  # one force refresh at first fallback

    fake_clock.set_ms(1_025_000)
    _ = clock.now_ms()
    assert rest.calls == 2  # blocked by cooldown, no extra refresh
    assert clock.state.state == "degraded"

    fake_clock.set_ms(1_051_000)
    _ = clock.now_ms()
    assert rest.calls == 3  # cooldown elapsed, refresh allowed
