from __future__ import annotations

from dark_alpha_phase_one.config import load_settings


def test_load_settings_parses_test_emit_mode(monkeypatch) -> None:
    monkeypatch.setenv("SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("TEST_EMIT_ENABLED", "1")
    monkeypatch.setenv("TEST_EMIT_SYMBOLS", "ethusdt, solusdt")
    monkeypatch.setenv("TEST_EMIT_INTERVAL_SEC", "15")
    monkeypatch.setenv("TEST_EMIT_TF", "5m")

    settings = load_settings()

    assert settings.test_emit_enabled is True
    assert settings.test_emit_symbols == ["ETHUSDT", "SOLUSDT"]
    assert settings.test_emit_interval_sec == 15
    assert settings.test_emit_tf == "5m"
