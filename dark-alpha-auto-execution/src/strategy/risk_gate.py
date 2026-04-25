"""Final pre-execution gate (spec Section 4.3.5).

Runs AFTER validator + sizer succeed, just before a ticket is persisted.
Checks global/account state — the things the per-signal logic cannot see.

  1. Kill switch active?                        → reject
  2. Circuit breaker tripped (halt action)?     → reject
  3. Open positions at cap?                     → reject
  4. Duplicate symbol (unless allowed)?         → reject
  5. Daily ticket cap reached?                  → reject
  6. Equity below min_equity_to_trade?          → reject
"""

from datetime import UTC, datetime
from pathlib import Path

import structlog

from safety.circuit_breaker import CircuitBreaker
from safety.kill_switch import KillSwitch, get_kill_switch
from signal_adapter.schemas import SetupEvent
from storage.db import get_db

from .config import risk_gate_config
from .schemas import Rejection

log = structlog.get_logger(__name__)

_HALT_ACTIONS = {"halt_24h", "halt_12h", "halt_until_manual_reset"}


class RiskGate:
    def __init__(
        self,
        kill_switch: KillSwitch | None = None,
        breaker: CircuitBreaker | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._ks = kill_switch or get_kill_switch()
        self._cb = breaker or CircuitBreaker(db_path=db_path)
        self._db_path = db_path

    def check(self, event: SetupEvent, equity_usd: float) -> Rejection | None:
        cfg = risk_gate_config()
        max_positions = int(cfg.get("max_concurrent_positions", 3))
        max_tickets_per_day = int(cfg.get("max_tickets_per_day", 5))
        min_equity = float(cfg.get("min_equity_to_trade", 1000.0))
        allow_dup = bool(cfg.get("allow_duplicate_symbol", False))

        if self._ks.is_active():
            return self._reject(event, "kill_switch_active", "")

        action = self._cb.get_active_action()
        if action in _HALT_ACTIONS:
            return self._reject(event, "circuit_breaker_halt", f"action={action}")
        if action == "no_new_entries":
            return self._reject(event, "no_new_entries", "circuit breaker advisory")

        if equity_usd < min_equity:
            return self._reject(
                event,
                "below_min_equity",
                f"{equity_usd:.2f} < {min_equity:.2f}",
            )

        open_count, open_symbols = self._open_positions()
        if open_count >= max_positions:
            return self._reject(
                event,
                "max_positions_reached",
                f"{open_count} >= {max_positions}",
            )
        if not allow_dup and event.symbol in open_symbols:
            return self._reject(event, "duplicate_symbol", event.symbol)

        if self._tickets_today() >= max_tickets_per_day:
            return self._reject(
                event,
                "daily_ticket_cap",
                f">= {max_tickets_per_day}",
            )

        return None

    def _open_positions(self) -> tuple[int, set[str]]:
        try:
            with get_db(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT symbol FROM positions WHERE status IN ('pending','open','partial')"
                ).fetchall()
            symbols = {row["symbol"] for row in rows}
            return len(symbols), symbols
        except Exception as exc:  # noqa: BLE001
            log.warning("risk_gate.open_positions_failed", error=str(exc))
            return 0, set()

    def _tickets_today(self) -> int:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        try:
            with get_db(self._db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM execution_tickets "
                    "WHERE substr(created_at,1,10)=?",
                    (today,),
                ).fetchone()
            return int(row["n"]) if row else 0
        except Exception as exc:  # noqa: BLE001
            log.warning("risk_gate.tickets_today_failed", error=str(exc))
            return 0

    def _reject(self, event: SetupEvent, reason: str, detail: str) -> Rejection:
        log.warning(
            "risk_gate.reject",
            event_id=event.event_id,
            reason=reason,
            detail=detail,
        )
        return Rejection(
            source_event_id=event.event_id,
            stage="risk_gate",
            reason=reason,
            detail=detail,
        )
