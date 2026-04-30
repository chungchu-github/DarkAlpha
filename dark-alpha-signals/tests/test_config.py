from __future__ import annotations

import sys
from pathlib import Path

from dark_alpha_phase_one import config as config_module
from dark_alpha_phase_one.config import load_settings


def test_load_settings_loads_workspace_root_env_after_package_env(monkeypatch) -> None:
    """signals must read both its own .env and the monorepo root .env so
    rotating a shared secret (e.g. Telegram token) requires updating
    only the workspace-level file. Package .env still wins for keys
    present in both, matching auto-execution's bootstrap pattern.
    """
    calls: list[Path] = []

    def recording_load_dotenv(*args: object, **kwargs: object) -> bool:
        if args:
            calls.append(Path(str(args[0])))
        # second positional or 'override' kwarg should be False
        override = kwargs.get("override", args[1] if len(args) > 1 else None)
        assert override is False, f"override must be False, got {override!r}"
        return False

    monkeypatch.setitem(sys.modules["dotenv"].__dict__, "load_dotenv", recording_load_dotenv)
    monkeypatch.setattr(config_module, "load_dotenv", recording_load_dotenv)

    load_settings()

    assert len(calls) == 2, f"expected two load_dotenv calls, got {calls}"
    package_call, workspace_call = calls
    assert package_call.name == ".env"
    assert package_call.parent.name == "dark-alpha-signals"
    assert workspace_call.name == ".env"
    assert workspace_call.parent == package_call.parent.parent


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


def test_load_settings_parses_max_leverage_display(monkeypatch) -> None:
    monkeypatch.setenv("MAX_LEVERAGE_DISPLAY", "4")

    settings = load_settings()

    assert settings.max_leverage_display == 4
