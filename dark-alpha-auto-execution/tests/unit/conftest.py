"""Shared fixtures for strategy + execution tests."""

from pathlib import Path

import pytest

from signal_adapter.schemas import InvalidationInfo, SetupEvent, TriggerInfo


@pytest.fixture()
def setup_event() -> SetupEvent:
    return SetupEvent(
        event_id="evt-test-001",
        timestamp="2026-04-18T02:00:00+00:00",
        symbol="BTCUSDT-PERP",
        setup_type="active",
        direction="long",
        regime="vol_breakout_card",
        today_decision="breakout",
        ranking_score=7.85,
        trigger=TriggerInfo(condition="breakout", price_level=100.0, timeframe="15m"),
        invalidation=InvalidationInfo(condition="stop", price_level=99.0),
        metadata={},
    )


@pytest.fixture()
def short_event() -> SetupEvent:
    return SetupEvent(
        event_id="evt-test-short",
        timestamp="2026-04-18T02:00:00+00:00",
        symbol="ETHUSDT-PERP",
        setup_type="active",
        direction="short",
        regime="fake_breakout_reversal",
        today_decision="fade",
        ranking_score=8.0,
        trigger=TriggerInfo(condition="trigger", price_level=100.0, timeframe="15m"),
        invalidation=InvalidationInfo(condition="stop", price_level=101.0),
        metadata={},
    )


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(p))
    return p
