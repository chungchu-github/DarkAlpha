"""Integration tests for the FastAPI signal receiver.

Uses TestClient (synchronous httpx) to hit the actual ASGI app.
SQLite is redirected to a tmp_path to keep tests hermetic.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def client(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DB_PATH", str(db_path))
    # Re-import app after env var is set so get_db picks up the new path
    from signal_adapter.receiver import app

    return TestClient(app)


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
    "rationale": "BTC breakout above compression zone",
    "created_at": "2026-04-18T02:00:00+00:00",
    "priority": 40,
    "confidence": 78.5,
    "oi_status": "fresh",
    "trace_id": "test-trace-001",
}


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_post_valid_signal_returns_200(client: TestClient) -> None:
    resp = client.post("/signal", json=VALID_PAYLOAD)
    assert resp.status_code == 200


def test_post_valid_signal_returns_event_id(client: TestClient) -> None:
    resp = client.post("/signal", json=VALID_PAYLOAD)
    data = resp.json()
    assert data["event_id"] == "test-trace-001"
    assert data["symbol"] == "BTCUSDT-PERP"


def test_post_signal_persists_to_sqlite(client: TestClient, db_path: Path) -> None:
    client.post("/signal", json=VALID_PAYLOAD)

    import sqlite3

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT event_id, symbol FROM setup_events").fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "test-trace-001"
    assert row[1] == "BTCUSDT-PERP"


def test_post_duplicate_signal_is_ignored(client: TestClient) -> None:
    resp1 = client.post("/signal", json=VALID_PAYLOAD)
    resp2 = client.post("/signal", json=VALID_PAYLOAD)
    assert resp1.status_code == 200
    assert resp2.status_code == 200  # INSERT OR IGNORE — no error


def test_post_invalid_json_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/signal",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_post_missing_required_field_returns_422(client: TestClient) -> None:
    bad = {k: v for k, v in VALID_PAYLOAD.items() if k != "symbol"}
    resp = client.post("/signal", json=bad)
    assert resp.status_code == 422


def test_fixture_file_round_trip(client: TestClient) -> None:
    fixture = Path(__file__).parent.parent / "fixtures" / "sample_proposal_card.json"
    payload = json.loads(fixture.read_text())
    resp = client.post("/signal", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_id"] == "abc123def456"
    assert data["symbol"] == "BTCUSDT-PERP"
