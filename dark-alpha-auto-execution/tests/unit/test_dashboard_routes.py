"""Integration tests for the dashboard FastAPI app.

These tests use FastAPI's TestClient. The TestClient defaults the client
host to "testclient", which the localhost middleware would reject; we
override that to a loopback address for the green-path tests, then test
the rejection path explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app
from storage.db import init_db


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db = tmp_path / "dashboard.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    # TestClient defaults to client.host="testclient" which our localhost
    # middleware would (correctly) reject; the `client` kwarg overrides
    # the ASGI scope's client tuple so green-path tests look loopback.
    return TestClient(app, client=("127.0.0.1", 8766))


# --------------------------------------------------------------------------
# Endpoint contract — every panel returns 200 with the expected shape
# --------------------------------------------------------------------------


def test_index_serves_dashboard_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "<title>Dark Alpha Live Monitor</title>" in body
    # Each panel must have its data-panel slot present
    for panel in (
        "kpis",
        "positions",
        "tickets",
        "reconcile",
        "heartbeat",
        "breakers",
        "halts",
        "gate6",
        "equity",
    ):
        assert f'data-panel="{panel}"' in body, f"missing panel slot: {panel}"


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_kpis_shape(client: TestClient) -> None:
    r = client.get("/api/kpis")
    assert r.status_code == 200
    d = r.json()
    for key in (
        "kill_switch",
        "mode",
        "environment",
        "allow_mainnet",
        "mainnet_armed",
        "today_pnl",
        "open_live_positions",
        "last_reconcile",
    ):
        assert key in d, f"kpis missing {key}"
    assert "active" in d["kill_switch"]


def test_api_list_endpoints_return_lists(client: TestClient) -> None:
    for path in (
        "/api/positions",
        "/api/tickets",
        "/api/reconcile",
        "/api/breakers",
        "/api/halts",
        "/api/equity",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert isinstance(r.json(), list), path


def test_api_heartbeat_shape(client: TestClient) -> None:
    r = client.get("/api/heartbeat")
    assert r.status_code == 200
    d = r.json()
    assert {"status", "created_at", "age_seconds"} <= d.keys()


def test_api_gate6_shape(client: TestClient) -> None:
    r = client.get("/api/gate6")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] in {"go", "no_go"}
    assert isinstance(d["checks"], list)
    assert "markdown" in d


# --------------------------------------------------------------------------
# Localhost middleware — non-loopback request must be rejected
# --------------------------------------------------------------------------


def test_non_localhost_host_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TestClient lets us spoof the source address via the ASGI scope.

    We rebuild a minimal request and call the middleware directly, which
    is the deterministic way to verify the host check (TestClient's own
    transport always reports loopback).
    """
    db = tmp_path / "dashboard.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)

    from dashboard.middleware import LocalhostOnlyMiddleware

    captured: dict[str, int] = {}

    async def call_next(_request):  # pragma: no cover - should not be called
        captured["called"] = 1
        return None

    class _DummyClient:
        def __init__(self, host: str) -> None:
            self.host = host

    class _DummyRequest:
        def __init__(self, host: str) -> None:
            self.client = _DummyClient(host)

    middleware = LocalhostOnlyMiddleware(app=app)
    import asyncio

    response = asyncio.run(middleware.dispatch(_DummyRequest("8.8.8.8"), call_next))  # type: ignore[arg-type]
    assert response.status_code == 403
    assert "called" not in captured

    # Sanity: loopback gets through (call_next would have been called).
    async def call_next_ok(_request):
        captured["ok"] = 1
        return "passed"

    out = asyncio.run(middleware.dispatch(_DummyRequest("127.0.0.1"), call_next_ok))  # type: ignore[arg-type]
    assert out == "passed"
    assert captured.get("ok") == 1
