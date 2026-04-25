"""Supervisor loop — the 7×24 runtime for shadow mode.

Every `eval_interval_sec` seconds:
  - check kill switch (halt if active)
  - run one PositionEvaluator tick
  - if UTC date has rolled over, write yesterday's daily snapshot

Designed to be launched by systemd/launchd/tmux. Signal-safe: SIGINT/SIGTERM
flip an internal flag and the loop exits cleanly at the next tick boundary.
"""

import signal
import time
from datetime import UTC, date, datetime
from pathlib import Path
from types import FrameType

import structlog

from execution.evaluator import PositionEvaluator
from reporting.daily import write_snapshot
from safety.kill_switch import get_kill_switch

log = structlog.get_logger(__name__)

_DEFAULT_INTERVAL_SEC = 30


class Supervisor:
    def __init__(
        self,
        evaluator: PositionEvaluator | None = None,
        eval_interval_sec: int = _DEFAULT_INTERVAL_SEC,
        db_path: Path | None = None,
    ) -> None:
        self._eval = evaluator or PositionEvaluator(db_path=db_path)
        self._interval = eval_interval_sec
        self._db_path = db_path
        self._stop = False
        self._last_snapshot_date: date | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, max_ticks: int | None = None) -> int:
        """Main loop. Returns number of ticks executed. max_ticks is for tests."""
        self._install_signal_handlers()
        log.info("supervisor.start", interval_sec=self._interval)

        ticks = 0
        while not self._stop:
            self._tick_once()
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            self._sleep(self._interval)
        log.info("supervisor.stop", ticks=ticks)
        return ticks

    def request_stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tick_once(self) -> None:
        ks = get_kill_switch()
        if ks.is_active():
            log.warning("supervisor.halted_by_kill_switch")
            return

        self._maybe_write_daily_snapshot()
        try:
            results = self._eval.tick()
            closed = [r for r in results if r.triggered]
            if closed:
                log.info("supervisor.tick", closed_count=len(closed))
        except Exception as exc:  # noqa: BLE001
            log.error("supervisor.tick_failed", error=str(exc))

    def _maybe_write_daily_snapshot(self) -> None:
        today = datetime.now(tz=UTC).date()
        if self._last_snapshot_date is None:
            self._last_snapshot_date = today
            return
        if today > self._last_snapshot_date:
            # day boundary crossed → snapshot yesterday
            try:
                write_snapshot(self._last_snapshot_date, db_path=self._db_path)
            except Exception as exc:  # noqa: BLE001
                log.error("supervisor.snapshot_failed", error=str(exc))
            self._last_snapshot_date = today

    def _sleep(self, seconds: float) -> None:
        # Granular sleep so request_stop() is honored within the interval
        deadline = time.monotonic() + seconds
        while not self._stop and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def _install_signal_handlers(self) -> None:
        def _handler(_signum: int, _frame: FrameType | None) -> None:
            log.warning("supervisor.signal_received", signum=_signum)
            self._stop = True

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except ValueError:
            # Not in main thread (tests) — skip.
            pass

    # Exposed for tests
    def set_last_snapshot_date(self, d: date | None) -> None:
        self._last_snapshot_date = d


def run_supervisor(interval_sec: int = _DEFAULT_INTERVAL_SEC) -> None:
    """Entry point for CLI / long-running process."""
    Supervisor(eval_interval_sec=interval_sec).run()


__all__ = ["Supervisor", "run_supervisor"]
