"""Circuit breaker — automatic trading halts based on configurable rules.

Rules are loaded from config/breakers.yaml. State is persisted in SQLite
so a process restart does not clear an active trip.

Supported actions (spec Section 4.4.2):
  halt_24h              — stop all new tickets for 24 hours
  halt_12h              — stop all new tickets for 12 hours
  halt_until_manual_reset — stop until operator calls reset()
  no_new_entries        — allow position management, but no new entries
  halve_position_size   — reduce sizing (advisory flag, sizer reads this)
"""

import contextlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger(__name__)

_CONFIG_PATH = Path(os.getenv("CONFIG_DIR", "config")) / "breakers.yaml"

_HALT_ACTIONS = {"halt_24h", "halt_12h", "halt_until_manual_reset"}
_HALT_DURATIONS: dict[str, timedelta | None] = {
    "halt_24h": timedelta(hours=24),
    "halt_12h": timedelta(hours=12),
    "halt_until_manual_reset": None,  # None = no auto-clear
    "no_new_entries": None,
    "halve_position_size": None,
}


@dataclass
class BreakerState:
    name: str
    status: str  # "ok" | "tripped"
    reason: str
    action: str
    tripped_at: str | None
    clear_at: str | None  # ISO8601 UTC — None means manual reset only


class CircuitBreaker:
    def __init__(
        self,
        db_path: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._db_path = db_path
        self._cfg_path = config_path or _CONFIG_PATH
        self._rules: dict[str, dict[str, str]] = self._load_rules()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_tripped(self) -> bool:
        """Return True if any halt-action breaker is currently active."""
        self._auto_clear_expired()
        for state in self._load_all_states().values():
            if state.status == "tripped" and state.action in _HALT_ACTIONS:
                return True
        return False

    def get_active_action(self) -> str | None:
        """Return the action of the highest-priority tripped breaker, or None."""
        self._auto_clear_expired()
        for state in self._load_all_states().values():
            if state.status == "tripped":
                return state.action
        return None

    def trip(self, name: str, reason: str) -> None:
        """Persist a trip for the named breaker."""
        action = self._rules.get(name, {}).get("action", "halt_until_manual_reset")
        now = datetime.now(tz=UTC)
        duration = _HALT_DURATIONS.get(action)
        clear_at = (now + duration).isoformat() if duration else None

        self._upsert_state(
            BreakerState(
                name=name,
                status="tripped",
                reason=reason,
                action=action,
                tripped_at=now.isoformat(),
                clear_at=clear_at,
            )
        )
        log.critical(
            "circuit_breaker.TRIPPED",
            name=name,
            action=action,
            reason=reason,
            clear_at=clear_at,
        )
        try:
            self._send_alert(name, action, reason)
        except Exception as exc:  # noqa: BLE001
            log.warning("circuit_breaker.alert_send_failed", error=str(exc))

    def reset(self, name: str) -> None:
        """Manually clear a breaker."""
        self._upsert_state(
            BreakerState(
                name=name,
                status="ok",
                reason="",
                action=self._rules.get(name, {}).get("action", ""),
                tripped_at=None,
                clear_at=None,
            )
        )
        log.warning("circuit_breaker.reset", name=name)

    def reset_all(self) -> None:
        for name in self._rules:
            self.reset(name)

    def get_state(self, name: str) -> BreakerState | None:
        self._auto_clear_expired()
        states = self._load_all_states()
        return states.get(name)

    def all_states(self) -> dict[str, BreakerState]:
        self._auto_clear_expired()
        return self._load_all_states()

    def rule_names(self) -> list[str]:
        return list(self._rules.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_rules(self) -> dict[str, dict[str, str]]:
        try:
            with open(self._cfg_path) as f:
                data = yaml.safe_load(f)
            return {b["name"]: b for b in data.get("breakers", [])}
        except FileNotFoundError:
            log.warning("circuit_breaker.config_not_found", path=str(self._cfg_path))
            return {}

    def _get_db(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
        from storage.db import get_db

        return get_db(self._db_path)

    def _upsert_state(self, state: BreakerState) -> None:
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO circuit_breaker_state
                    (name, status, reason, action, tripped_at, clear_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    status=excluded.status,
                    reason=excluded.reason,
                    action=excluded.action,
                    tripped_at=excluded.tripped_at,
                    clear_at=excluded.clear_at
                """,
                (
                    state.name,
                    state.status,
                    state.reason,
                    state.action,
                    state.tripped_at,
                    state.clear_at,
                ),
            )
            conn.commit()

    def _load_all_states(self) -> dict[str, BreakerState]:
        try:
            with self._get_db() as conn:
                rows = conn.execute(
                    "SELECT name, status, reason, action, tripped_at, clear_at "
                    "FROM circuit_breaker_state"
                ).fetchall()
            return {
                row["name"]: BreakerState(
                    name=row["name"],
                    status=row["status"],
                    reason=row["reason"] or "",
                    action=row["action"] or "",
                    tripped_at=row["tripped_at"],
                    clear_at=row["clear_at"],
                )
                for row in rows
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("circuit_breaker.load_states_failed", error=str(exc))
            return {}

    def _auto_clear_expired(self) -> None:
        """Clear any time-based breakers whose clear_at has passed."""
        now = datetime.now(tz=UTC).isoformat()
        try:
            with self._get_db() as conn:
                rows = conn.execute(
                    "SELECT name FROM circuit_breaker_state "
                    "WHERE status='tripped' AND clear_at IS NOT NULL AND clear_at <= ?",
                    (now,),
                ).fetchall()
                for row in rows:
                    log.info("circuit_breaker.auto_cleared", name=row["name"])
                conn.execute(
                    "UPDATE circuit_breaker_state SET status='ok', reason='', "
                    "tripped_at=NULL, clear_at=NULL "
                    "WHERE status='tripped' AND clear_at IS NOT NULL AND clear_at <= ?",
                    (now,),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("circuit_breaker.auto_clear_failed", error=str(exc))

    def _send_alert(self, name: str, action: str, reason: str) -> None:
        try:
            from observability.notifier import send_alert

            send_alert("CRITICAL", f"⚡ CIRCUIT BREAKER TRIPPED: {name} → {action} ({reason})")
        except Exception as exc:  # noqa: BLE001
            log.warning("circuit_breaker.alert_failed", error=str(exc))
