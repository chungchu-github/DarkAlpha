"""Unit tests for KillSwitch — ≥90% coverage required (spec Phase 1)."""

from pathlib import Path

import pytest

from safety.kill_switch import KillSwitch


@pytest.fixture()
def sentinel(tmp_path: Path) -> Path:
    return tmp_path / "test-kill"


@pytest.fixture()
def ks(sentinel: Path) -> KillSwitch:
    return KillSwitch(sentinel_path=sentinel)


# ------------------------------------------------------------------
# Initial state
# ------------------------------------------------------------------


def test_initially_inactive(ks: KillSwitch) -> None:
    assert not ks.is_active()


def test_sentinel_path_returned(ks: KillSwitch, sentinel: Path) -> None:
    assert ks.sentinel_path() == sentinel


# ------------------------------------------------------------------
# Trigger method 1: programmatic activate()
# ------------------------------------------------------------------


def test_activate_sets_active(ks: KillSwitch) -> None:
    ks.activate(reason="test")
    assert ks.is_active()


def test_activate_creates_sentinel_file(ks: KillSwitch, sentinel: Path) -> None:
    ks.activate(reason="test")
    assert sentinel.exists()


def test_deactivate_clears_active(ks: KillSwitch) -> None:
    ks.activate(reason="test")
    ks.deactivate()
    assert not ks.is_active()


def test_deactivate_removes_sentinel_file(ks: KillSwitch, sentinel: Path) -> None:
    ks.activate(reason="test")
    ks.deactivate()
    assert not sentinel.exists()


def test_deactivate_when_not_active_is_safe(ks: KillSwitch) -> None:
    ks.deactivate()  # must not raise
    assert not ks.is_active()


# ------------------------------------------------------------------
# Trigger method 2: file sentinel (external process creates the file)
# ------------------------------------------------------------------


def test_file_sentinel_triggers_is_active(ks: KillSwitch, sentinel: Path) -> None:
    sentinel.touch()  # simulate: external process / CLI touched the file
    assert ks.is_active()


def test_removing_sentinel_file_clears_without_deactivate(ks: KillSwitch, sentinel: Path) -> None:
    sentinel.touch()
    assert ks.is_active()
    sentinel.unlink()
    assert not ks.is_active()


def test_file_sentinel_without_in_memory_flag(tmp_path: Path) -> None:
    sentinel = tmp_path / "ext-kill"
    ks = KillSwitch(sentinel_path=sentinel)
    assert not ks._active  # flag not set
    sentinel.touch()
    assert ks.is_active()  # file check detects it


# ------------------------------------------------------------------
# Idempotency
# ------------------------------------------------------------------


def test_activate_twice_is_safe(ks: KillSwitch, sentinel: Path) -> None:
    ks.activate(reason="first")
    ks.activate(reason="second")
    assert ks.is_active()
    assert sentinel.exists()


def test_deactivate_twice_is_safe(ks: KillSwitch) -> None:
    ks.activate(reason="test")
    ks.deactivate()
    ks.deactivate()  # must not raise
    assert not ks.is_active()


# ------------------------------------------------------------------
# Alert failure does not propagate
# ------------------------------------------------------------------


def test_activate_succeeds_even_if_alert_fails(
    ks: KillSwitch, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad_alert(level: str, msg: str) -> None:
        raise RuntimeError("telegram down")

    monkeypatch.setattr("safety.kill_switch.KillSwitch._send_alert", bad_alert)
    ks.activate(reason="test")  # must not raise
    assert ks.is_active()


# ------------------------------------------------------------------
# Default sentinel path
# ------------------------------------------------------------------


def test_default_sentinel_is_tmp_path() -> None:
    ks = KillSwitch()
    assert str(ks.sentinel_path()).startswith("/tmp/")
