"""Gate 6.4-6.8 readiness review.

This module turns the remaining Gate 6 requirements into concrete database
checks. It does not place orders. It answers the operator question: is this
runtime ready to continue micro-live, or should it stay halted?
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ulid import ULID

from safety.kill_switch import KillSwitch, get_kill_switch
from storage.db import get_db

from .binance_testnet_broker import normalize_symbol
from .live_event_guard import symbols_with_unprotected_live_positions


@dataclass(frozen=True)
class Gate6ReadinessCheck:
    gate: str
    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class Gate6ReadinessReport:
    report_id: str
    status: str
    checks: list[Gate6ReadinessCheck] = field(default_factory=list)

    def markdown(self) -> str:
        lines = [
            "# Gate 6.8 Go/No-Go Review",
            "",
            f"- report_id: `{self.report_id}`",
            f"- status: `{self.status}`",
            "",
            "| Gate | Check | Status | Detail |",
            "|---|---|---|---|",
        ]
        for check in self.checks:
            lines.append(f"| {check.gate} | {check.name} | `{check.status}` | {check.detail} |")
        return "\n".join(lines)


class Gate6ReadinessReviewer:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        kill_switch: KillSwitch | None = None,
        now: datetime | None = None,
    ) -> None:
        self._db_path = db_path
        self._kill_switch = kill_switch or get_kill_switch()
        self._now = now or datetime.now(tz=UTC)

    def run(
        self,
        *,
        symbols: list[str] | None = None,
        require_recent_stream_minutes: int = 30,
        require_burn_in_hours: int = 24,
        persist: bool = True,
    ) -> Gate6ReadinessReport:
        target_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        checks = [
            self._check_schema(),
            self._check_user_stream_events(target_symbols, require_recent_stream_minutes),
            self._check_runtime_heartbeat(require_recent_stream_minutes),
            self._check_reconciliation_ok(target_symbols),
            self._check_event_guard_state(),
            self._check_open_positions_protected(),
            self._check_burn_in(target_symbols, require_burn_in_hours),
            self._check_kill_switch_clear(),
        ]
        status = "go" if all(check.status == "ok" for check in checks) else "no_go"
        report = Gate6ReadinessReport(report_id=str(ULID()), status=status, checks=checks)
        if persist:
            self._persist(report)
        return report

    def _check_schema(self) -> Gate6ReadinessCheck:
        needed = {"live_stream_events", "live_runtime_heartbeats", "gate6_readiness_reports"}
        with get_db(self._db_path) as conn:
            tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        missing = sorted(needed - tables)
        if missing:
            return Gate6ReadinessCheck("6.4", "schema installed", "fail", ",".join(missing))
        return Gate6ReadinessCheck("6.4", "schema installed", "ok")

    def _check_user_stream_events(
        self,
        symbols: list[str] | None,
        recent_minutes: int,
    ) -> Gate6ReadinessCheck:
        cutoff = _sqlite_ts(self._now - timedelta(minutes=recent_minutes))
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT symbol, execution_type, order_status, processed_at
                  FROM live_stream_events
                 WHERE execution_type='TRADE'
                   AND datetime(processed_at) >= datetime(?)
                """,
                (cutoff,),
            ).fetchall()
        matching = [
            row
            for row in rows
            if symbols is None or normalize_symbol(str(row["symbol"])) in symbols
        ]
        if not matching:
            return Gate6ReadinessCheck(
                "6.4",
                "recent fill events ingested",
                "fail",
                f"no TRADE user-stream events in last {recent_minutes}m",
            )
        return Gate6ReadinessCheck(
            "6.4",
            "recent fill events ingested",
            "ok",
            f"{len(matching)} event(s)",
        )

    def _check_runtime_heartbeat(self, recent_minutes: int) -> Gate6ReadinessCheck:
        cutoff = _sqlite_ts(self._now - timedelta(minutes=recent_minutes))
        with get_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT status, created_at
                  FROM live_runtime_heartbeats
                 WHERE component='user_stream'
                   AND datetime(created_at) >= datetime(?)
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (cutoff,),
            ).fetchone()
        if row is None:
            return Gate6ReadinessCheck(
                "6.5",
                "user stream heartbeat",
                "fail",
                f"no heartbeat in last {recent_minutes}m",
            )
        return Gate6ReadinessCheck("6.5", "user stream heartbeat", "ok", str(row["status"]))

    def _check_reconciliation_ok(self, symbols: list[str] | None) -> Gate6ReadinessCheck:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT status, details, created_at
                  FROM reconciliation_runs
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            ).fetchone()
        if row is None:
            return Gate6ReadinessCheck("6.5", "latest reconciliation", "fail", "no reconciliation run")
        if str(row["status"]) != "ok":
            return Gate6ReadinessCheck("6.5", "latest reconciliation", "fail", str(row["status"]))
        if symbols:
            details = json.loads(str(row["details"] or "{}"))
            reconciled = {
                normalize_symbol(str(item.get("symbol", "")))
                for item in details.get("symbols", [])
                if isinstance(item, dict)
            }
            missing = sorted(set(symbols) - reconciled)
            if missing:
                return Gate6ReadinessCheck("6.5", "latest reconciliation", "fail", "missing:" + ",".join(missing))
        return Gate6ReadinessCheck("6.5", "latest reconciliation", "ok", str(row["created_at"]))

    def _check_event_guard_state(self) -> Gate6ReadinessCheck:
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT reason
                  FROM audit_log
                 WHERE event_type='live_event_guard_halt'
                 ORDER BY created_at DESC
                 LIMIT 3
                """
            ).fetchall()
        if rows:
            return Gate6ReadinessCheck("6.6", "event-driven guard", "fail", ";".join(str(r["reason"]) for r in rows))
        return Gate6ReadinessCheck("6.6", "event-driven guard", "ok")

    def _check_open_positions_protected(self) -> Gate6ReadinessCheck:
        unprotected = symbols_with_unprotected_live_positions(self._db_path)
        if unprotected:
            return Gate6ReadinessCheck("6.6", "open positions protected", "fail", ",".join(unprotected))
        return Gate6ReadinessCheck("6.6", "open positions protected", "ok")

    def _check_burn_in(self, symbols: list[str] | None, required_hours: int) -> Gate6ReadinessCheck:
        cutoff = _sqlite_ts(self._now - timedelta(hours=required_hours))
        with get_db(self._db_path) as conn:
            event_rows = conn.execute(
                """
                SELECT symbol
                  FROM live_stream_events
                 WHERE execution_type='TRADE'
                   AND datetime(processed_at) >= datetime(?)
                """,
                (cutoff,),
            ).fetchall()
            reconcile_rows = conn.execute(
                """
                SELECT status
                  FROM reconciliation_runs
                 WHERE datetime(created_at) >= datetime(?)
                """,
                (cutoff,),
            ).fetchall()
        events = [
            row
            for row in event_rows
            if symbols is None or normalize_symbol(str(row["symbol"])) in symbols
        ]
        ok_reconciles = [row for row in reconcile_rows if str(row["status"]) == "ok"]
        if not events or not ok_reconciles:
            return Gate6ReadinessCheck(
                "6.7",
                "burn-in evidence",
                "fail",
                f"requires {required_hours}h window with stream events and ok reconciliation",
            )
        return Gate6ReadinessCheck(
            "6.7",
            "burn-in evidence",
            "ok",
            f"events={len(events)}, reconciliations={len(ok_reconciles)}",
        )

    def _check_kill_switch_clear(self) -> Gate6ReadinessCheck:
        if self._kill_switch.is_active():
            return Gate6ReadinessCheck("6.8", "kill switch clear", "fail", str(self._kill_switch.sentinel_path()))
        return Gate6ReadinessCheck("6.8", "kill switch clear", "ok")

    def _persist(self, report: Gate6ReadinessReport) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO gate6_readiness_reports (report_id, status, details)
                VALUES (?, ?, ?)
                """,
                (
                    report.report_id,
                    report.status,
                    json.dumps(
                        {
                            "status": report.status,
                            "checks": [check.__dict__ for check in report.checks],
                        },
                        sort_keys=True,
                    ),
                ),
            )
            conn.commit()


def _sqlite_ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
