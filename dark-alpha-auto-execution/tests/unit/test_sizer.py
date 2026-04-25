"""Unit tests for strategy.sizer."""

import pytest

from signal_adapter.schemas import InvalidationInfo, SetupEvent, TriggerInfo
from strategy import config
from strategy.schemas import Rejection
from strategy.sizer import SizingResult, size


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    config.clear_cache()


def test_basic_long_sizing(setup_event: SetupEvent) -> None:
    result = size(setup_event, equity_usd=10_000.0, gate="gate1")
    assert isinstance(result, SizingResult)
    # risk = 10000 * 0.005 = 50; stop_dist = 1.0; raw_qty = 50.0; step 0.001 → 50.0
    assert result.quantity == pytest.approx(50.0)
    assert result.notional_usd == pytest.approx(5000.0)
    assert result.leverage == pytest.approx(0.5)
    assert result.risk_usd == pytest.approx(50.0)


def test_basic_short_sizing(short_event: SetupEvent) -> None:
    result = size(short_event, equity_usd=10_000.0, gate="gate1")
    assert isinstance(result, SizingResult)
    assert result.quantity > 0


def test_leverage_cap_exceeded(setup_event: SetupEvent) -> None:
    # Tight stop forces a huge notional (50 risk / 0.001 dist = 50000 qty → 10_000_000 notional)
    tight = setup_event.model_copy(
        update={
            "trigger": TriggerInfo(condition="t", price_level=200.0, timeframe="15m"),
            "invalidation": InvalidationInfo(condition="s", price_level=199.999),
        }
    )
    result = size(tight, equity_usd=10_000.0, gate="gate1")
    assert isinstance(result, Rejection)
    assert result.reason in {"leverage_cap_exceeded", "above_hard_cap"}


def test_below_min_notional(setup_event: SetupEvent) -> None:
    # Tiny equity → tiny risk → qty rounds to step → notional tiny
    result = size(setup_event, equity_usd=1.0, gate="gate1")
    assert isinstance(result, Rejection)
    assert result.reason in {"qty_below_step", "below_min_notional"}


def test_stop_distance_zero(setup_event: SetupEvent) -> None:
    zero_dist = setup_event.model_copy(
        update={
            "invalidation": InvalidationInfo(
                condition="s", price_level=setup_event.trigger.price_level  # type: ignore[union-attr]
            )
        }
    )
    result = size(zero_dist, equity_usd=10_000.0, gate="gate1")
    assert isinstance(result, Rejection)
    assert result.reason == "stop_distance_too_small"


def test_missing_levels_rejection(setup_event: SetupEvent) -> None:
    event = setup_event.model_copy(update={"trigger": None})
    result = size(event, equity_usd=10_000.0, gate="gate1")
    assert isinstance(result, Rejection)
    assert result.reason == "missing_levels"
