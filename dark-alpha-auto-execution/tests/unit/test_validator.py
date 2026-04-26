"""Unit tests for strategy.validator."""

import pytest

from signal_adapter.schemas import InvalidationInfo, SetupEvent, TriggerInfo
from strategy import config, validator


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    config.clear_cache()


def test_valid_event_passes(setup_event: SetupEvent) -> None:
    assert validator.validate(setup_event) is None


def test_low_score_rejected(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(update={"ranking_score": 5.0})
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "low_ranking_score"


def test_blocked_regime_rejected(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(update={"regime": "chaos"})
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "blocked_regime"


def test_not_active_rejected(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(update={"setup_type": "alert"})
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "not_active"


def test_missing_direction_rejected(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(update={"direction": None})
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "no_direction"


def test_unhealthy_market_data_rejected(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(
        update={
            "metadata": {
                "ttl_minutes": 60,  # keep TTL valid so freshness gate doesn't fire first
                "data_health": {"status": "blocked", "reason": "oi_stale"},
            }
        }
    )
    rej = validator.validate(event)
    assert rej is not None
    assert rej.reason == "data_unhealthy"
    assert rej.detail == "oi_stale"


def test_long_stop_wrong_side(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(
        update={"invalidation": InvalidationInfo(condition="stop", price_level=101.0)}
    )
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "stop_wrong_side"


def test_short_stop_wrong_side(short_event: SetupEvent) -> None:
    event = short_event.model_copy(
        update={"invalidation": InvalidationInfo(condition="stop", price_level=99.0)}
    )
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "stop_wrong_side"


def test_invalid_price_rejected(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(
        update={"trigger": TriggerInfo(condition="t", price_level=0.0, timeframe="15m")}
    )
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "invalid_price"


def test_missing_levels_rejected(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(update={"trigger": None})
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "missing_levels"


def test_trading_hours_disabled_by_default(setup_event: SetupEvent) -> None:
    # default config has enabled: false — should not reject
    assert validator.validate(setup_event) is None


# ---------------------------------------------------------------------------
# Task 9 — Signal TTL / freshness gate
# ---------------------------------------------------------------------------


def test_expired_signal_rejected(setup_event: SetupEvent) -> None:
    """A signal whose ttl has elapsed must be rejected."""
    from datetime import UTC, datetime, timedelta

    stale_ts = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
    event = setup_event.model_copy(update={"timestamp": stale_ts, "metadata": {"ttl_minutes": 15}})
    rej = validator.validate(event)
    assert rej is not None
    assert rej.reason == "signal_expired"
    assert rej.stage == "validator"


def test_valid_ttl_signal_accepted(setup_event: SetupEvent) -> None:
    """A signal within its ttl window must pass the freshness gate."""
    from datetime import UTC, datetime

    fresh_ts = datetime.now(tz=UTC).isoformat()
    event = setup_event.model_copy(update={"timestamp": fresh_ts, "metadata": {"ttl_minutes": 30}})
    assert validator.validate(event) is None


def test_missing_ttl_rejected(setup_event: SetupEvent) -> None:
    """Missing ttl_minutes must fail closed regardless of mode."""
    from datetime import UTC, datetime

    event = setup_event.model_copy(
        update={"timestamp": datetime.now(tz=UTC).isoformat(), "metadata": {}}
    )
    rej = validator.validate(event)
    assert rej is not None
    assert rej.reason == "missing_signal_ttl"


def test_zero_ttl_rejected(setup_event: SetupEvent) -> None:
    """ttl_minutes <= 0 must be treated as missing."""
    from datetime import UTC, datetime

    event = setup_event.model_copy(
        update={
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "metadata": {"ttl_minutes": 0},
        }
    )
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "missing_signal_ttl"


def test_invalid_ttl_type_rejected(setup_event: SetupEvent) -> None:
    """ttl_minutes that cannot be coerced to int must fail closed."""
    from datetime import UTC, datetime

    event = setup_event.model_copy(
        update={
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "metadata": {"ttl_minutes": "not-a-number"},
        }
    )
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "missing_signal_ttl"


def test_invalid_timestamp_rejected(setup_event: SetupEvent) -> None:
    """Unparseable timestamp must reject before TTL is even considered."""
    event = setup_event.model_copy(
        update={"timestamp": "not-an-iso-date", "metadata": {"ttl_minutes": 15}}
    )
    rej = validator.validate(event)
    assert rej is not None
    assert rej.reason == "invalid_signal_timestamp"


def test_naive_timestamp_rejected(setup_event: SetupEvent) -> None:
    """Naive timestamp without timezone must reject — we cannot compare safely."""
    event = setup_event.model_copy(
        update={"timestamp": "2026-04-18T02:00:00", "metadata": {"ttl_minutes": 15}}
    )
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "invalid_signal_timestamp"


def test_empty_timestamp_rejected(setup_event: SetupEvent) -> None:
    """Empty timestamp string must fail closed."""
    event = setup_event.model_copy(update={"timestamp": "", "metadata": {"ttl_minutes": 15}})
    rej = validator.validate(event)
    assert rej is not None and rej.reason == "invalid_signal_timestamp"


def test_future_timestamp_beyond_skew_rejected(setup_event: SetupEvent) -> None:
    """A signal timestamped well into the future must reject (clock attack)."""
    from datetime import UTC, datetime, timedelta

    future_ts = (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat()
    event = setup_event.model_copy(update={"timestamp": future_ts, "metadata": {"ttl_minutes": 60}})
    rej = validator.validate(event)
    assert rej is not None
    assert rej.reason == "signal_timestamp_in_future"


def test_small_clock_skew_tolerated(setup_event: SetupEvent) -> None:
    """A signal a few seconds in the future must not be rejected (normal drift)."""
    from datetime import UTC, datetime, timedelta

    near_future = (datetime.now(tz=UTC) + timedelta(seconds=5)).isoformat()
    event = setup_event.model_copy(
        update={"timestamp": near_future, "metadata": {"ttl_minutes": 60}}
    )
    assert validator.validate(event) is None
