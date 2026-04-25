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
