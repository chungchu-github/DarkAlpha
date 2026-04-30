"""Regression tests for Gate 6.4-6.8 readiness reviewer.

These tests pin the three semantic adjustments made after the first 24h
burn-in's `no_go` post-mortem (see
``docs/incidents/2026-04-26-bracket-reject-orphan-position.md`` and the
follow-up commit):

1. ``_check_event_guard_state`` is bounded by ``require_burn_in_hours`` —
   historical halts that age out of the window must not poison new runs.
2. ``_check_user_stream_events`` returns ok (with explanatory detail) when
   no organic TRADE events landed in the window. Trade frequency is a
   strategy/market signal, not a safety signal; stream uptime is covered
   by the 6.5 heartbeat check.
3. ``_check_burn_in`` requires only ok reconciliations, not also organic
   trade events. Burn-in evidence is uptime + safety-chain integrity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from execution.gate6_readiness import Gate6ReadinessReviewer
from safety.kill_switch import KillSwitch
from storage.db import get_db, init_db


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "readiness.db"
    monkeypatch.setenv("DB_PATH", str(path))
    init_db(path)
    return path


def _ks(tmp_path: Path) -> KillSwitch:
    return KillSwitch(sentinel_path=tmp_path / "kill")


def _insert_halt(
    db: Path,
    *,
    ts: datetime,
    reason: str = "live_position_missing_protective_orders:BTCUSDT-PERP:t1:stop",
) -> None:
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO audit_log
                (event_type, event_id, source, decision, reason, metadata, created_at)
               VALUES ('live_event_guard_halt', 't1', 'live_event_guard',
                       'activate', ?, '{}', ?)""",
            (reason, ts.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def _insert_reconcile(db: Path, *, ts: datetime, status: str = "ok") -> None:
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO reconciliation_runs (run_id, status, details, created_at)
               VALUES (?, ?, '{}', ?)""",
            (f"run-{ts.timestamp()}", status, ts.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def _insert_heartbeat(db: Path, *, ts: datetime) -> None:
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO live_runtime_heartbeats
                (component, status, details, created_at)
               VALUES ('user_stream', 'listen_key_keepalive', '', ?)""",
            (ts.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Bug 1: event-guard halt must be bounded by the burn-in window
# ---------------------------------------------------------------------------


def test_event_guard_halt_outside_window_does_not_fail(db: Path, tmp_path: Path) -> None:
    """A halt recorded 30 hours ago must not fail a fresh 24h readiness run.

    Without the time bound, every historical halt — including ones from
    cleaned-up incidents — would pin this check at fail forever.
    """
    now = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
    _insert_halt(db, ts=now - timedelta(hours=30))

    report = Gate6ReadinessReviewer(db_path=db, kill_switch=_ks(tmp_path), now=now).run(
        require_burn_in_hours=24, persist=False
    )

    guard_check = next(c for c in report.checks if c.name == "event-driven guard")
    assert guard_check.status == "ok", guard_check.detail


def test_event_guard_halt_inside_window_fails(db: Path, tmp_path: Path) -> None:
    """A halt recorded 6 hours ago must fail a 24h readiness run."""
    now = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
    _insert_halt(
        db, ts=now - timedelta(hours=6), reason="live_position_missing_protective_orders:X:t1:stop"
    )

    report = Gate6ReadinessReviewer(db_path=db, kill_switch=_ks(tmp_path), now=now).run(
        require_burn_in_hours=24, persist=False
    )

    guard_check = next(c for c in report.checks if c.name == "event-driven guard")
    assert guard_check.status == "fail"
    assert "live_position_missing_protective_orders" in guard_check.detail


# ---------------------------------------------------------------------------
# Bug 2: no organic trade events must not block readiness
# ---------------------------------------------------------------------------


def test_no_trade_events_returns_ok_with_quiet_detail(db: Path, tmp_path: Path) -> None:
    """A quiet 30m window with no fill events is operationally clean.
    Trade frequency is a strategy/market outcome; the safety chain's
    uptime is evidenced by the 6.5 heartbeat check, not by this one.
    """
    now = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
    _insert_heartbeat(db, ts=now - timedelta(minutes=2))

    report = Gate6ReadinessReviewer(db_path=db, kill_switch=_ks(tmp_path), now=now).run(
        require_recent_stream_minutes=30, persist=False
    )

    fill_check = next(c for c in report.checks if c.name == "recent fill events ingested")
    assert fill_check.status == "ok"
    assert "no organic trade activity" in fill_check.detail


# ---------------------------------------------------------------------------
# Bug 3: burn-in evidence requires only ok reconciliation runs
# ---------------------------------------------------------------------------


def test_burn_in_evidence_ok_with_reconciles_only(db: Path, tmp_path: Path) -> None:
    """A 24h window with ok reconciliations and zero organic trades is
    valid burn-in evidence. The previous behaviour required at least one
    TRADE user-stream event, which made low-volatility windows unable to
    qualify regardless of how cleanly the system ran."""
    now = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
    for hours_ago in (1, 6, 12, 18, 23):
        _insert_reconcile(db, ts=now - timedelta(hours=hours_ago), status="ok")

    report = Gate6ReadinessReviewer(db_path=db, kill_switch=_ks(tmp_path), now=now).run(
        require_burn_in_hours=24, persist=False
    )

    burn_in_check = next(c for c in report.checks if c.name == "burn-in evidence")
    assert burn_in_check.status == "ok", burn_in_check.detail
    assert "reconciliations=5" in burn_in_check.detail
    assert "organic_trades=0" in burn_in_check.detail


def test_burn_in_evidence_fails_without_any_reconciles(db: Path, tmp_path: Path) -> None:
    """Reconcile runs are the load-bearing uptime evidence. No reconcile
    runs in the window → fail (the supervisor wasn't alive)."""
    now = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
    # Reconcile run before the window — should not count
    _insert_reconcile(db, ts=now - timedelta(hours=30))

    report = Gate6ReadinessReviewer(db_path=db, kill_switch=_ks(tmp_path), now=now).run(
        require_burn_in_hours=24, persist=False
    )

    burn_in_check = next(c for c in report.checks if c.name == "burn-in evidence")
    assert burn_in_check.status == "fail"
    assert "ok reconciliation runs" in burn_in_check.detail
