"""Additional kill switch tests to reach ≥90% coverage on safety modules."""

from pathlib import Path

import pytest

from safety.kill_switch import KillSwitch, get_kill_switch


def test_activate_handles_oserror_on_sentinel_touch(tmp_path: Path) -> None:
    sentinel = tmp_path / "subdir_does_not_exist" / "kill"
    ks = KillSwitch(sentinel_path=sentinel)
    # sentinel.touch() will fail because parent dir doesn't exist
    # activate() must not raise — it logs the error and continues
    ks.activate(reason="test oserror")
    assert ks._active is True  # in-memory flag still set


def test_deactivate_handles_oserror_on_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "kill"
    sentinel.touch()
    ks = KillSwitch(sentinel_path=sentinel)
    ks._active = True

    # Make unlink() raise
    def bad_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", bad_unlink)
    ks.deactivate()  # must not raise
    assert ks._active is False  # flag cleared even if file removal failed


def test_send_alert_inner_exception_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test the except block inside _send_alert itself."""
    sentinel = tmp_path / "kill"
    ks = KillSwitch(sentinel_path=sentinel)

    # Make send_alert raise so the except block in _send_alert fires
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake")

    import observability.notifier as notifier_mod

    def raise_always(level: str, msg: str) -> None:
        raise RuntimeError("network error")

    monkeypatch.setattr(notifier_mod, "send_alert", raise_always)
    ks._send_alert("test")  # must not raise


def test_get_kill_switch_returns_singleton() -> None:
    ks1 = get_kill_switch()
    ks2 = get_kill_switch()
    assert ks1 is ks2


def test_get_kill_switch_returns_kill_switch_instance() -> None:
    ks = get_kill_switch()
    assert isinstance(ks, KillSwitch)
