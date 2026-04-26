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

import json
from datetime import UTC, datetime, timedelta
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


class RiskGateDataError(RuntimeError):
    """Raised internally when a risk-state query fails.

    Converted to a fail-closed Rejection by ``RiskGate.check``. The reason
    string is one of the audit-defined names (``open_positions_unavailable``,
    ``ticket_count_unavailable``, ``pnl_state_unavailable``,
    ``risk_state_unavailable``).
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


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
        """Run all gate checks. Always returns a Rejection or None — never raises.

        Any DB / state-source failure is converted into a fail-closed Rejection
        with one of the audit reason codes so the receiver never sees a stray
        exception from this layer.
        """
        try:
            return self._check(event, equity_usd)
        except RiskGateDataError as exc:
            return self._reject(event, exc.reason, exc.detail)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "risk_gate.unexpected_failure",
                event_id=event.event_id,
                error=str(exc),
            )
            return self._reject(event, "risk_state_unavailable", str(exc))

    def _check(self, event: SetupEvent, equity_usd: float) -> Rejection | None:
        cfg = risk_gate_config()
        max_positions = int(cfg.get("max_concurrent_positions", 3))
        max_tickets_per_day = int(cfg.get("max_tickets_per_day", 5))
        max_symbol_tickets = int(cfg.get("max_tickets_per_symbol_per_day", 0))
        max_strategy_tickets = int(cfg.get("max_tickets_per_strategy_per_day", 0))
        max_consecutive_losses = int(cfg.get("max_consecutive_losses", 0))
        max_weekly_loss_pct = float(cfg.get("max_weekly_loss_pct", 0.0))
        max_daily_loss_usd = float(cfg.get("max_daily_loss_usd", 0.0))
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

        if max_consecutive_losses > 0 and self._consecutive_losses() >= max_consecutive_losses:
            return self._reject(
                event,
                "max_consecutive_losses",
                f">= {max_consecutive_losses}",
            )

        weekly_loss = self._weekly_realized_loss_usd()
        weekly_loss_cap = equity_usd * max_weekly_loss_pct
        if max_weekly_loss_pct > 0 and weekly_loss >= weekly_loss_cap:
            return self._reject(
                event,
                "weekly_loss_cap",
                f"{weekly_loss:.2f} >= {weekly_loss_cap:.2f}",
            )

        if max_daily_loss_usd > 0:
            daily_loss = self._daily_realized_loss_usd()
            if daily_loss >= max_daily_loss_usd:
                return self._reject(
                    event,
                    "daily_loss_cap",
                    f"{daily_loss:.2f} >= {max_daily_loss_usd:.2f}",
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
        if (
            max_symbol_tickets > 0
            and self._tickets_today_for_symbol(event.symbol) >= max_symbol_tickets
        ):
            return self._reject(
                event,
                "daily_symbol_ticket_cap",
                f"{event.symbol} >= {max_symbol_tickets}",
            )
        if (
            max_strategy_tickets > 0
            and self._tickets_today_for_strategy(event.regime) >= max_strategy_tickets
        ):
            return self._reject(
                event,
                "daily_strategy_ticket_cap",
                f"{event.regime} >= {max_strategy_tickets}",
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
            log.error("risk_gate.open_positions_failed", error=str(exc))
            raise RiskGateDataError("open_positions_unavailable", str(exc)) from exc

    def _tickets_today(self) -> int:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        try:
            with get_db(self._db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM execution_tickets WHERE substr(created_at,1,10)=?",
                    (today,),
                ).fetchone()
            return int(row["n"]) if row else 0
        except Exception as exc:  # noqa: BLE001
            log.error("risk_gate.tickets_today_failed", error=str(exc))
            raise RiskGateDataError("ticket_count_unavailable", str(exc)) from exc

    def _tickets_today_for_symbol(self, symbol: str) -> int:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        try:
            with get_db(self._db_path) as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                      FROM execution_tickets t
                      JOIN setup_events e ON e.event_id = t.source_event_id
                     WHERE substr(t.created_at,1,10)=?
                       AND e.symbol=?
                    """,
                    (today, symbol),
                ).fetchone()
            return int(row["n"]) if row else 0
        except Exception as exc:  # noqa: BLE001
            log.error("risk_gate.symbol_tickets_today_failed", error=str(exc))
            raise RiskGateDataError("ticket_count_unavailable", str(exc)) from exc

    def _tickets_today_for_strategy(self, strategy: str) -> int:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        try:
            with get_db(self._db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT e.payload
                      FROM execution_tickets t
                      JOIN setup_events e ON e.event_id = t.source_event_id
                     WHERE substr(t.created_at,1,10)=?
                    """,
                    (today,),
                ).fetchall()
            count = 0
            for row in rows:
                payload = str(row["payload"] or "")
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    parsed = {}
                if parsed.get("regime") == strategy:
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            log.error("risk_gate.strategy_tickets_today_failed", error=str(exc))
            raise RiskGateDataError("ticket_count_unavailable", str(exc)) from exc

    def _consecutive_losses(self) -> int:
        try:
            with get_db(self._db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT net_pnl_usd
                      FROM positions
                     WHERE status='closed'
                       AND net_pnl_usd IS NOT NULL
                     ORDER BY closed_at DESC
                    """
                ).fetchall()
            losses = 0
            for row in rows:
                pnl = float(row["net_pnl_usd"] or 0.0)
                if pnl < 0:
                    losses += 1
                    continue
                break
            return losses
        except Exception as exc:  # noqa: BLE001
            log.error("risk_gate.consecutive_losses_failed", error=str(exc))
            raise RiskGateDataError("pnl_state_unavailable", str(exc)) from exc

    def _daily_realized_loss_usd(self) -> float:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        try:
            with get_db(self._db_path) as conn:
                row = conn.execute(
                    """
                    SELECT COALESCE(
                              SUM(CASE WHEN net_pnl_usd < 0 THEN -net_pnl_usd ELSE 0 END),
                              0
                           ) AS loss
                      FROM positions
                     WHERE status='closed'
                       AND closed_at IS NOT NULL
                       AND substr(closed_at,1,10) = ?
                    """,
                    (today,),
                ).fetchone()
            return float(row["loss"] or 0.0) if row else 0.0
        except Exception as exc:  # noqa: BLE001
            log.error("risk_gate.daily_loss_failed", error=str(exc))
            raise RiskGateDataError("pnl_state_unavailable", str(exc)) from exc

    def _weekly_realized_loss_usd(self) -> float:
        now = datetime.now(tz=UTC)
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        try:
            with get_db(self._db_path) as conn:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(CASE WHEN net_pnl_usd < 0 THEN -net_pnl_usd ELSE 0 END), 0) AS loss
                      FROM positions
                     WHERE status='closed'
                       AND closed_at IS NOT NULL
                       AND substr(closed_at,1,10) >= ?
                    """,
                    (week_start,),
                ).fetchone()
            return float(row["loss"] or 0.0) if row else 0.0
        except Exception as exc:  # noqa: BLE001
            log.error("risk_gate.weekly_loss_failed", error=str(exc))
            raise RiskGateDataError("pnl_state_unavailable", str(exc)) from exc

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
