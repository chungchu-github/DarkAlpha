"""Pure read functions for the dashboard panels.

Every function returns a JSON-serialisable dict (or list of dicts). No
side effects: never call ``write_snapshot`` or anything that mutates DB
state from a panel handler — readers can be hammered every 5 seconds
without producing audit-log noise or accidental row updates.

The functions are deliberately thin — they reuse existing classes
(``KillSwitch``, ``CircuitBreaker``, ``Gate6ReadinessReviewer``) and
existing ``storage.db.get_db()`` so logic stays single-sourced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from execution.gate6_readiness import Gate6ReadinessReviewer
from execution.live_safety import load_live_execution_config
from safety.circuit_breaker import CircuitBreaker
from safety.kill_switch import get_kill_switch
from storage.db import get_db


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _age_seconds(ts_str: str | None) -> float | None:
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str.replace(" ", "T").replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None
    return (_now() - ts).total_seconds()


def kpis(db_path: Path | None = None) -> dict[str, Any]:
    """Top KPI row: kill switch, mode, mainnet armed, today PnL, open count, last reconcile."""
    ks = get_kill_switch()
    cfg = load_live_execution_config()
    mainnet_armed = (
        cfg.mode == "live"
        and cfg.environment == "mainnet"
        and cfg.allow_mainnet
        and bool(cfg.micro_live.get("enabled", False))
    )

    sentinel = ks.sentinel_path()
    sentinel_mtime: str | None = None
    if sentinel.exists():
        sentinel_mtime = datetime.fromtimestamp(sentinel.stat().st_mtime, tz=UTC).isoformat()

    today_iso = _now().date().isoformat()
    with get_db(db_path) as conn:
        snap = conn.execute(
            "SELECT net_pnl, trade_count, win_count FROM daily_snapshots WHERE date=?",
            (today_iso,),
        ).fetchone()
        open_count = conn.execute(
            """SELECT COUNT(*) AS n FROM positions
                WHERE shadow_mode=0 AND status IN ('pending','open','partial')"""
        ).fetchone()["n"]
        last_reconcile = conn.execute(
            "SELECT status, created_at FROM reconciliation_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    return {
        "kill_switch": {
            "active": ks.is_active(),
            "sentinel_path": str(sentinel),
            "sentinel_mtime": sentinel_mtime,
        },
        "mode": cfg.mode,
        "environment": cfg.environment,
        "allow_mainnet": cfg.allow_mainnet,
        "micro_live_enabled": bool(cfg.micro_live.get("enabled", False)),
        "mainnet_armed": mainnet_armed,
        "today_pnl": {
            "date": today_iso,
            "net_pnl": float(snap["net_pnl"]) if snap and snap["net_pnl"] is not None else None,
            "trade_count": int(snap["trade_count"]) if snap else 0,
            "win_count": int(snap["win_count"]) if snap else 0,
        },
        "open_live_positions": int(open_count),
        "last_reconcile": {
            "status": str(last_reconcile["status"]) if last_reconcile else None,
            "created_at": str(last_reconcile["created_at"]) if last_reconcile else None,
            "age_seconds": _age_seconds(last_reconcile["created_at"]) if last_reconcile else None,
        },
    }


def live_positions(db_path: Path | None = None) -> list[dict[str, Any]]:
    """Currently-open live positions (shadow_mode=0)."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            """SELECT position_id, ticket_id, symbol, direction, status,
                      filled_quantity, entry_price, stop_price, take_profit_price,
                      opened_at
                 FROM positions
                WHERE shadow_mode=0 AND status IN ('pending','open','partial')
                ORDER BY opened_at DESC"""
        ).fetchall()
    return [
        {
            "position_id": str(r["position_id"]),
            "ticket_id": str(r["ticket_id"]) if r["ticket_id"] else None,
            "symbol": str(r["symbol"]),
            "direction": str(r["direction"]),
            "status": str(r["status"]),
            "filled_quantity": float(r["filled_quantity"] or 0.0),
            "entry_price": float(r["entry_price"]) if r["entry_price"] is not None else None,
            "stop_price": float(r["stop_price"]) if r["stop_price"] is not None else None,
            "take_profit_price": (
                float(r["take_profit_price"]) if r["take_profit_price"] is not None else None
            ),
            "opened_at": str(r["opened_at"]) if r["opened_at"] else None,
            "age_seconds": _age_seconds(r["opened_at"]),
        }
        for r in rows
    ]


def recent_tickets(db_path: Path | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Last N execution tickets (mixed shadow + live)."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            """SELECT ticket_id, source_event_id, status, shadow_mode, reject_reason, created_at
                 FROM execution_tickets
                ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "ticket_id": str(r["ticket_id"]),
            "source_event_id": str(r["source_event_id"]) if r["source_event_id"] else None,
            "status": str(r["status"]),
            "shadow_mode": bool(r["shadow_mode"]),
            "reject_reason": str(r["reject_reason"]) if r["reject_reason"] else None,
            "created_at": str(r["created_at"]),
            "age_seconds": _age_seconds(r["created_at"]),
        }
        for r in rows
    ]


def reconcile_history(db_path: Path | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Last N reconciliation runs."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            """SELECT run_id, status, details, created_at
                 FROM reconciliation_runs
                ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "run_id": str(r["run_id"]),
            "status": str(r["status"]),
            "details": str(r["details"]) if r["details"] else None,
            "created_at": str(r["created_at"]),
            "age_seconds": _age_seconds(r["created_at"]),
        }
        for r in rows
    ]


def user_stream_heartbeat(db_path: Path | None = None) -> dict[str, Any]:
    """Latest user_stream heartbeat — single age in seconds."""
    with get_db(db_path) as conn:
        row = conn.execute(
            """SELECT status, created_at FROM live_runtime_heartbeats
                WHERE component='user_stream'
                ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
    if row is None:
        return {"status": None, "created_at": None, "age_seconds": None}
    return {
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "age_seconds": _age_seconds(row["created_at"]),
    }


def gate6_readiness(db_path: Path | None = None) -> dict[str, Any]:
    """Run a fresh readiness review (persist=False) and return the report."""
    reviewer = Gate6ReadinessReviewer(db_path=db_path)
    report = reviewer.run(persist=False)
    return {
        "report_id": report.report_id,
        "status": report.status,
        "checks": [
            {"gate": c.gate, "name": c.name, "status": c.status, "detail": c.detail}
            for c in report.checks
        ],
        "markdown": report.markdown(),
    }


def circuit_breakers(db_path: Path | None = None) -> list[dict[str, Any]]:
    """Current circuit breaker states. Empty list if all ok and no rows."""
    breaker = CircuitBreaker(db_path=db_path)
    states = breaker.all_states()
    return [
        {
            "name": s.name,
            "status": s.status,
            "reason": s.reason,
            "action": s.action,
            "tripped_at": s.tripped_at,
            "clear_at": s.clear_at,
        }
        for s in states.values()
    ]


def recent_halts(db_path: Path | None = None, lookback_hours: int = 24) -> list[dict[str, Any]]:
    """Halt-class audit_log events in the last N hours."""
    cutoff = (_now() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db(db_path) as conn:
        rows = conn.execute(
            """SELECT event_type, source, decision, reason, created_at
                 FROM audit_log
                WHERE event_type IN ('live_event_guard_halt','kill_switch_activated',
                                     'circuit_breaker_tripped')
                  AND datetime(created_at) >= datetime(?)
                ORDER BY created_at DESC LIMIT 50""",
            (cutoff,),
        ).fetchall()
    return [
        {
            "event_type": str(r["event_type"]),
            "source": str(r["source"]) if r["source"] else None,
            "decision": str(r["decision"]) if r["decision"] else None,
            "reason": str(r["reason"]) if r["reason"] else None,
            "created_at": str(r["created_at"]),
            "age_seconds": _age_seconds(r["created_at"]),
        }
        for r in rows
    ]


def equity_sparkline(db_path: Path | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Last N equity_snapshots for footer sparkline."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            """SELECT ts, equity_usd FROM equity_snapshots
                ORDER BY ts DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    # Flip back to chronological order for plotting
    return [{"ts": str(r["ts"]), "equity_usd": float(r["equity_usd"])} for r in reversed(rows)]
