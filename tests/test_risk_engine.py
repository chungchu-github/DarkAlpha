from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dark_alpha_phase_one.risk_engine import RiskEngine


def test_cooldown_blocks_same_symbol_within_window(tmp_path) -> None:
    state_file = tmp_path / "risk_state.json"
    engine = RiskEngine(
        state_path=str(state_file),
        max_daily_loss_usdt=30,
        max_cards_per_day=5,
        cooldown_after_trigger_minutes=30,
        kill_switch=False,
    )

    now = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
    assert engine.evaluate("BTCUSDT", now=now).allowed

    engine.record_trigger("BTCUSDT", now=now)
    blocked = engine.evaluate("BTCUSDT", now=now + timedelta(minutes=5))
    allowed_later = engine.evaluate("BTCUSDT", now=now + timedelta(minutes=31))

    assert blocked.allowed is False
    assert blocked.reason == "symbol_cooldown_active"
    assert allowed_later.allowed is True


def test_max_cards_per_day_blocks_after_limit(tmp_path) -> None:
    state_file = tmp_path / "risk_state.json"
    engine = RiskEngine(
        state_path=str(state_file),
        max_daily_loss_usdt=30,
        max_cards_per_day=2,
        cooldown_after_trigger_minutes=1,
        kill_switch=False,
    )

    now = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
    engine.record_trigger("BTCUSDT", now=now)
    engine.record_trigger("ETHUSDT", now=now + timedelta(minutes=2))

    decision = engine.evaluate("BNBUSDT", now=now + timedelta(minutes=3))
    assert decision.allowed is False
    assert decision.reason == "max_cards_per_day_exceeded"
