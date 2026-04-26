"""Event-driven live safety checks for Gate 6.6.

The user-data stream is the primary fill path from Gate 6.3 onward. These
checks run after fill ingestion so unsafe live state pauses new entries
immediately instead of waiting for a later reconciliation pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from safety.audit import log_event
from safety.kill_switch import KillSwitch, get_kill_switch
from storage.db import get_db
from strategy.schemas import ExecutionTicket

from .binance_testnet_broker import normalize_symbol

log = structlog.get_logger(__name__)

_PROTECTIVE_ACTIVE_STATUSES = {"submitted", "acknowledged"}


@dataclass(frozen=True)
class LiveEventGuardResult:
    status: str
    findings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class LiveEventGuard:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self._db_path = db_path
        self._kill_switch = kill_switch or get_kill_switch()

    def inspect_ticket_after_fill(
        self, ticket: ExecutionTicket, order_role: str
    ) -> LiveEventGuardResult:
        findings: list[str] = []
        if order_role == "entry" and self._has_active_live_position(ticket.ticket_id):
            missing = self._missing_protective_roles(ticket.ticket_id)
            if missing:
                findings.append(
                    f"live_position_missing_protective_orders:{ticket.symbol}:{ticket.ticket_id}:"
                    + ",".join(missing)
                )
        if findings:
            self._halt(";".join(findings), event_id=ticket.ticket_id)
            return LiveEventGuardResult("halted", findings)
        return LiveEventGuardResult("ok")

    def record_untracked_fill(
        self,
        *,
        symbol: str,
        client_order_id: str,
        fill_delta: float,
        allow_emergency_close: bool,
    ) -> LiveEventGuardResult:
        if fill_delta <= 0:
            return LiveEventGuardResult("ok")
        reason = f"live_untracked_order_fill:{symbol}:{client_order_id}"
        log_event(
            event_type="live_untracked_order_fill",
            source="live_event_guard",
            decision="record",
            reason=reason,
            event_id=client_order_id,
            metadata={
                "symbol": symbol,
                "client_order_id": client_order_id,
                "fill_delta": fill_delta,
                "allow_emergency_close": allow_emergency_close,
            },
            db_path=self._db_path,
        )
        if allow_emergency_close:
            log.warning(
                "live_event_guard.emergency_close_fill",
                symbol=symbol,
                client_order_id=client_order_id,
                fill_delta=fill_delta,
            )
            return LiveEventGuardResult("ok", [reason])
        self._halt(reason, event_id=client_order_id)
        return LiveEventGuardResult("halted", [reason])

    def inspect_all_active_positions(self) -> LiveEventGuardResult:
        findings: list[str] = []
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT ticket_id, symbol
                  FROM positions
                 WHERE shadow_mode=0
                   AND status IN ('open','partial')
                   AND ticket_id IS NOT NULL
                """
            ).fetchall()
        for row in rows:
            ticket_id = str(row["ticket_id"])
            missing = self._missing_protective_roles(ticket_id)
            if missing:
                findings.append(
                    f"live_position_missing_protective_orders:{row['symbol']}:{ticket_id}:"
                    + ",".join(missing)
                )
        if findings:
            self._halt(";".join(findings), event_id=None)
            return LiveEventGuardResult("halted", findings)
        return LiveEventGuardResult("ok")

    def _has_active_live_position(self, ticket_id: str) -> bool:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT 1
                  FROM positions
                 WHERE ticket_id=?
                   AND shadow_mode=0
                   AND status IN ('open','partial')
                 LIMIT 1
                """,
                (ticket_id,),
            ).fetchone()
        return row is not None

    def _missing_protective_roles(self, ticket_id: str) -> list[str]:
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT order_role, status
                  FROM order_idempotency
                 WHERE ticket_id=?
                   AND order_role IN ('stop','take_profit')
                """,
                (ticket_id,),
            ).fetchall()
        active = {
            str(row["order_role"])
            for row in rows
            if str(row["status"]) in _PROTECTIVE_ACTIVE_STATUSES
        }
        return [role for role in ("stop", "take_profit") if role not in active]

    def _halt(self, reason: str, *, event_id: str | None) -> None:
        log_event(
            event_type="live_event_guard_halt",
            source="live_event_guard",
            decision="activate",
            reason=reason,
            event_id=event_id,
            metadata={"reason": reason},
            db_path=self._db_path,
        )
        self._kill_switch.activate(reason=reason[:500])
        log.error("live_event_guard.halted", reason=reason)


def symbols_with_unprotected_live_positions(db_path: Path | None = None) -> list[str]:
    guard = LiveEventGuard(
        db_path=db_path, kill_switch=KillSwitch(sentinel_path=Path("/tmp/dark-alpha-readonly-kill"))
    )
    symbols: set[str] = set()
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.symbol, p.ticket_id
              FROM positions p
             WHERE p.shadow_mode=0
               AND p.status IN ('open','partial')
               AND p.ticket_id IS NOT NULL
            """
        ).fetchall()
    for row in rows:
        if guard._missing_protective_roles(str(row["ticket_id"])):  # noqa: SLF001
            symbols.add(normalize_symbol(str(row["symbol"])))
    return sorted(symbols)
