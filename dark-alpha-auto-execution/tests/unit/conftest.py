"""Shared fixtures for strategy + execution tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from signal_adapter.schemas import InvalidationInfo, SetupEvent, TriggerInfo


def _fresh_timestamp() -> str:
    """ISO8601 UTC 'now' — keeps fixtures inside the validator TTL window."""
    return datetime.now(tz=UTC).isoformat()


@pytest.fixture()
def setup_event() -> SetupEvent:
    return SetupEvent(
        event_id="evt-test-001",
        timestamp=_fresh_timestamp(),
        symbol="BTCUSDT-PERP",
        setup_type="active",
        direction="long",
        regime="vol_breakout_card",
        today_decision="breakout",
        ranking_score=7.85,
        trigger=TriggerInfo(condition="breakout", price_level=100.0, timeframe="15m"),
        invalidation=InvalidationInfo(condition="stop", price_level=99.0),
        metadata={"ttl_minutes": 60},
    )


@pytest.fixture()
def short_event() -> SetupEvent:
    return SetupEvent(
        event_id="evt-test-short",
        timestamp=_fresh_timestamp(),
        symbol="ETHUSDT-PERP",
        setup_type="active",
        direction="short",
        regime="fake_breakout_reversal",
        today_decision="fade",
        ranking_score=8.0,
        trigger=TriggerInfo(condition="trigger", price_level=100.0, timeframe="15m"),
        invalidation=InvalidationInfo(condition="stop", price_level=101.0),
        metadata={"ttl_minutes": 60},
    )


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(p))
    return p
