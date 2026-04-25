"""Tests for kill switch integration in the signal receiver."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from safety.kill_switch import KillSwitch

VALID_PAYLOAD = {
    "symbol": "BTCUSDT",
    "strategy": "vol_breakout_card",
    "side": "LONG",
    "entry": 94500.0,
    "stop": 93200.0,
    "leverage_suggest": 3,
    "position_usdt": 500.0,
    "max_risk_usdt": 25.0,
    "ttl_minutes": 15,
    "rationale": "BTC breakout",
    "created_at": "2026-04-18T02:00:00+00:00",
    "priority": 40,
    "confidence": 78.5,
    "oi_status": "fresh",
    "trace_id": "ks-test-001",
}


def _make_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kill_switch_active: bool,
) -> TestClient:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    sentinel = tmp_path / "sentinel"
    ks = KillSwitch(sentinel_path=sentinel)
    if kill_switch_active:
        sentinel.touch()

    import signal_adapter.receiver as receiver_mod

    monkeypatch.setattr(receiver_mod, "get_kill_switch", lambda: ks)

    from signal_adapter.receiver import app

    return TestClient(app)


def test_signal_accepted_when_kill_switch_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch, kill_switch_active=False)
    resp = client.post("/signal", json=VALID_PAYLOAD)
    assert resp.status_code == 200


def test_signal_rejected_when_kill_switch_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch, kill_switch_active=True)
    resp = client.post("/signal", json=VALID_PAYLOAD)
    assert resp.status_code == 503
    assert "kill switch" in resp.json()["detail"].lower()


def test_health_returns_halted_when_kill_switch_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch, kill_switch_active=True)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "halted"


def test_health_returns_ok_when_kill_switch_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch, kill_switch_active=False)
    resp = client.get("/health")
    assert resp.json()["status"] == "ok"
