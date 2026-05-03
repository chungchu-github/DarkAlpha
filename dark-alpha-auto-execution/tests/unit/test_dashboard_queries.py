"""Unit tests for the dashboard read-only query layer.

Each test seeds a tmp DB with init_db() + raw INSERTs of fixture rows
and asserts the query function returns the expected shape and key
values. Tests intentionally do not lock exact secondary fields like
ages — those drift with wall-clock and are checked structurally only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dashboard import queries
from storage.db import get_db, init_db


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "dashboard.db"
    monkeypatch.setenv("DB_PATH", str(path))
    init_db(path)
    return path


def _ts(offset_seconds: int = 0) -> str:
    """SQLite-style timestamp `offset_seconds` ago (negative → past)."""
    t = datetime.now(tz=UTC) + timedelta(seconds=offset_seconds)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _iso(offset_seconds: int = 0) -> str:
    """ISO-8601 timestamp with timezone."""
    t = datetime.now(tz=UTC) + timedelta(seconds=offset_seconds)
    return t.isoformat()


# --------------------------------------------------------------------------
# kpis()
# --------------------------------------------------------------------------


def test_kpis_empty_db_returns_safe_defaults(db: Path, tmp_path: Path) -> None:
    out = queries.kpis(db_path=db)
    assert "kill_switch" in out and "active" in out["kill_switch"]
    assert out["mode"] in {"shadow", "live"}
    assert out["environment"] in {"testnet", "mainnet"}
    assert out["open_live_positions"] == 0
    assert out["last_reconcile"]["status"] is None
    assert out["today_pnl"]["trade_count"] == 0


def test_kpis_counts_only_live_open_positions(db: Path) -> None:
    with get_db(db) as conn:
        # one live open, one shadow open, one live closed → only the live open counts
        for pid, shadow, status in [
            ("p-live-open", 0, "open"),
            ("p-shadow-open", 1, "open"),
            ("p-live-closed", 0, "closed"),
        ]:
            conn.execute(
                """INSERT INTO positions
                    (position_id, symbol, direction, status, shadow_mode,
                     quantity, filled_quantity, entry_price, opened_at)
                   VALUES (?, 'BTCUSDT-PERP', 'long', ?, ?, 0.01, 0.01, 100, ?)""",
                (pid, status, shadow, _iso(-60)),
            )
        conn.commit()
    out = queries.kpis(db_path=db)
    assert out["open_live_positions"] == 1


def test_kpis_picks_up_today_daily_snapshot(db: Path) -> None:
    today = datetime.now(tz=UTC).date().isoformat()
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO daily_snapshots
                (date, starting_equity, ending_equity, trade_count, win_count,
                 loss_count, gross_pnl, fees, net_pnl)
               VALUES (?, 10000, 10042.5, 3, 2, 1, 50, 7.5, 42.5)""",
            (today,),
        )
        conn.commit()
    out = queries.kpis(db_path=db)
    assert out["today_pnl"]["net_pnl"] == 42.5
    assert out["today_pnl"]["trade_count"] == 3
    assert out["today_pnl"]["win_count"] == 2


# --------------------------------------------------------------------------
# live_positions()
# --------------------------------------------------------------------------


def test_live_positions_excludes_shadow_and_closed(db: Path) -> None:
    with get_db(db) as conn:
        for pid, shadow, status in [
            ("p1", 0, "open"),
            ("p2", 0, "partial"),
            ("p3", 1, "open"),
            ("p4", 0, "closed"),
        ]:
            conn.execute(
                """INSERT INTO positions
                    (position_id, symbol, direction, status, shadow_mode,
                     quantity, filled_quantity, entry_price, opened_at)
                   VALUES (?, 'BTCUSDT-PERP', 'long', ?, ?, 0.01, 0.01, 100, ?)""",
                (pid, status, shadow, _iso(-60)),
            )
        conn.commit()
    out = queries.live_positions(db_path=db)
    assert {row["position_id"] for row in out} == {"p1", "p2"}


# --------------------------------------------------------------------------
# recent_tickets()
# --------------------------------------------------------------------------


def test_recent_tickets_orders_desc_and_caps_limit(db: Path) -> None:
    with get_db(db) as conn:
        # need a setup_event to satisfy FK
        conn.execute(
            """INSERT INTO setup_events
                (event_id, timestamp, symbol, setup_type, payload, received_at)
               VALUES ('evt-1', ?, 'BTCUSDT-PERP', 'active', '{}', ?)""",
            (_iso(-3600), _ts(-3600)),
        )
        for i in range(12):
            conn.execute(
                """INSERT INTO execution_tickets
                    (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
                   VALUES (?, 'evt-1', 'accepted', 0, '{}', ?)""",
                (f"t-{i:02d}", _iso(-i * 60)),
            )
        conn.commit()
    out = queries.recent_tickets(db_path=db, limit=10)
    assert len(out) == 10
    assert out[0]["ticket_id"] == "t-00"  # most recent first
    assert out[-1]["ticket_id"] == "t-09"


# --------------------------------------------------------------------------
# reconcile_history()
# --------------------------------------------------------------------------


def test_reconcile_history_returns_recent_first(db: Path) -> None:
    with get_db(db) as conn:
        for i, status in enumerate(["ok", "mismatch", "ok", "ok", "failed", "ok"]):
            conn.execute(
                """INSERT INTO reconciliation_runs (run_id, status, details, created_at)
                   VALUES (?, ?, '{}', ?)""",
                (f"r-{i}", status, _ts(-i * 60)),
            )
        conn.commit()
    out = queries.reconcile_history(db_path=db, limit=5)
    assert len(out) == 5
    assert [r["status"] for r in out] == ["ok", "mismatch", "ok", "ok", "failed"]


# --------------------------------------------------------------------------
# user_stream_heartbeat()
# --------------------------------------------------------------------------


def test_heartbeat_returns_none_when_empty(db: Path) -> None:
    out = queries.user_stream_heartbeat(db_path=db)
    assert out["status"] is None
    assert out["age_seconds"] is None


def test_heartbeat_picks_latest_user_stream_only(db: Path) -> None:
    with get_db(db) as conn:
        # newer non-user_stream entry must be ignored
        conn.execute(
            """INSERT INTO live_runtime_heartbeats (component, status, details, created_at)
               VALUES ('other', 'ok', '', ?)""",
            (_ts(-10),),
        )
        conn.execute(
            """INSERT INTO live_runtime_heartbeats (component, status, details, created_at)
               VALUES ('user_stream', 'listen_key_keepalive', '', ?)""",
            (_ts(-300),),
        )
        conn.commit()
    out = queries.user_stream_heartbeat(db_path=db)
    assert out["status"] == "listen_key_keepalive"
    assert out["age_seconds"] is not None
    assert 250 < out["age_seconds"] < 350  # ~5min, ±tolerance


# --------------------------------------------------------------------------
# circuit_breakers()
# --------------------------------------------------------------------------


def test_circuit_breakers_empty_when_no_state(db: Path) -> None:
    out = queries.circuit_breakers(db_path=db)
    assert out == []


# --------------------------------------------------------------------------
# recent_halts()
# --------------------------------------------------------------------------


def test_recent_halts_filters_by_event_type_and_window(db: Path) -> None:
    with get_db(db) as conn:
        # in-window halt
        conn.execute(
            """INSERT INTO audit_log
                (event_type, event_id, source, decision, reason, metadata, created_at)
               VALUES ('live_event_guard_halt', 'e1', 'guard', 'activate',
                       'orphan position', '{}', ?)""",
            (_ts(-3600),),
        )
        # out-of-window halt (older than 24h)
        conn.execute(
            """INSERT INTO audit_log
                (event_type, event_id, source, decision, reason, metadata, created_at)
               VALUES ('kill_switch_activated', 'e2', 'op', 'activate',
                       'old halt', '{}', ?)""",
            (_ts(-2 * 86400),),
        )
        # unrelated event in window
        conn.execute(
            """INSERT INTO audit_log
                (event_type, event_id, source, decision, reason, metadata, created_at)
               VALUES ('signal_received', 'e3', 'receiver', 'accept',
                       'normal flow', '{}', ?)""",
            (_ts(-60),),
        )
        conn.commit()
    out = queries.recent_halts(db_path=db, lookback_hours=24)
    assert len(out) == 1
    assert out[0]["event_type"] == "live_event_guard_halt"
    assert out[0]["reason"] == "orphan position"


# --------------------------------------------------------------------------
# equity_sparkline()
# --------------------------------------------------------------------------


def test_equity_sparkline_empty(db: Path) -> None:
    assert queries.equity_sparkline(db_path=db) == []


def test_equity_sparkline_chronological_oldest_first(db: Path) -> None:
    with get_db(db) as conn:
        for i, eq in enumerate([10000, 10010, 9990, 10020]):
            conn.execute(
                """INSERT INTO equity_snapshots
                    (ts, equity_usd, realized, unrealized, mode, gate)
                   VALUES (?, ?, 0, 0, 'live', 'gate1')""",
                (_iso(-(3 - i) * 60), eq),
            )
        conn.commit()
    out = queries.equity_sparkline(db_path=db, limit=10)
    assert [round(r["equity_usd"]) for r in out] == [10000, 10010, 9990, 10020]


# --------------------------------------------------------------------------
# gate6_readiness() — smoke (delegates to existing reviewer)
# --------------------------------------------------------------------------


def test_gate6_readiness_returns_full_report_shape(db: Path) -> None:
    out = queries.gate6_readiness(db_path=db)
    assert "report_id" in out
    assert out["status"] in {"go", "no_go"}
    assert isinstance(out["checks"], list) and len(out["checks"]) >= 4
    assert "markdown" in out and "Gate" in out["markdown"]
