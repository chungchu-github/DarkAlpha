"""Kill switch — the most important safety module in the system.

Three trigger methods (spec Section 4.4.1):
  1. Programmatic / CLI: ks.activate(reason)
  2. File sentinel:      touch /tmp/dark-alpha-kill  (checked on every is_active() call)
  3. Remote (Phase 8):   Telegram bot → calls activate() via webhook

Design constraints (Hard Don'ts #6):
  - MUST be synchronous — is_active() must return instantly, never awaits
  - Sentinel file is the IPC mechanism so other processes (CLI, cron) can trigger it

Default behaviour on activation:
  - Stops all new ticket generation (callers check is_active() before proceeding)
  - Does NOT auto-flatten positions (use the flatten command separately)
  - Sends an alert to all configured channels
"""

import os
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_SENTINEL = Path(os.getenv("KILL_SWITCH_SENTINEL", "/tmp/dark-alpha-kill"))


class KillSwitch:
    def __init__(self, sentinel_path: Path | None = None) -> None:
        self._sentinel: Path = sentinel_path or _DEFAULT_SENTINEL
        self._active: bool = False  # in-process flag (fast path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Synchronous check — hot path. Never raises."""
        return self._active or self._sentinel.exists()

    def activate(self, reason: str = "manual") -> None:
        """Activate the kill switch.

        Creates the sentinel file (survives process restart), sets the
        in-process flag, and emits a CRITICAL log entry.
        """
        self._active = True
        try:
            self._sentinel.touch()
        except OSError as exc:
            log.error("kill_switch.sentinel_write_failed", path=str(self._sentinel), error=str(exc))

        log.critical(
            "kill_switch.ACTIVATED",
            reason=reason,
            sentinel=str(self._sentinel),
        )
        try:
            self._send_alert(reason)
        except Exception as exc:  # noqa: BLE001
            log.warning("kill_switch.alert_send_failed", error=str(exc))

    def deactivate(self) -> None:
        """Clear the kill switch.

        Removes the sentinel file and clears the in-process flag.
        Only call after investigating the reason for activation.
        """
        self._active = False
        if self._sentinel.exists():
            try:
                self._sentinel.unlink()
            except OSError as exc:
                log.error(
                    "kill_switch.sentinel_remove_failed",
                    path=str(self._sentinel),
                    error=str(exc),
                )

        log.warning("kill_switch.cleared", sentinel=str(self._sentinel))

    def sentinel_path(self) -> Path:
        return self._sentinel

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send_alert(self, reason: str) -> None:
        """Best-effort alert — never raises, never blocks."""
        try:
            from observability.notifier import send_alert

            send_alert("CRITICAL", f"🚨 KILL SWITCH ACTIVATED — {reason}")
        except Exception as exc:  # noqa: BLE001
            log.warning("kill_switch.alert_failed", error=str(exc))


# Module-level singleton — import and use directly in hot paths
_instance: KillSwitch | None = None


def get_kill_switch() -> KillSwitch:
    """Return the module-level singleton KillSwitch."""
    global _instance  # noqa: PLW0603
    if _instance is None:
        _instance = KillSwitch()
    return _instance
