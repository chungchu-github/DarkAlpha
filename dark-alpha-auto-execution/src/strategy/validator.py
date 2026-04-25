"""Signal validator — first filter after ingestion.

Checks (spec Section 4.3):
  - setup_type must be 'active'
  - direction must be long/short (not None)
  - ranking_score >= min_ranking_score
  - regime not in blocked_regimes
  - trigger / invalidation prices present and on the correct side
  - trading_hours_utc (optional, disabled by default for crypto)
"""

from datetime import datetime, time

import structlog

from signal_adapter.schemas import SetupEvent

from .config import validator_config
from .schemas import Rejection

log = structlog.get_logger(__name__)


def validate(event: SetupEvent) -> Rejection | None:
    """Return None if the event should proceed, or a Rejection if blocked."""
    cfg = validator_config()
    min_score = float(cfg.get("min_ranking_score", 7.0))
    blocked = set(cfg.get("blocked_regimes", []))

    if event.setup_type != "active":
        return _reject(event, "not_active", f"setup_type={event.setup_type}")

    if event.direction not in {"long", "short"}:
        return _reject(event, "no_direction", f"direction={event.direction}")

    if event.ranking_score < min_score:
        return _reject(
            event,
            "low_ranking_score",
            f"{event.ranking_score:.2f} < {min_score:.2f}",
        )

    if event.regime in blocked:
        return _reject(event, "blocked_regime", f"regime={event.regime}")

    if event.trigger is None or event.invalidation is None:
        return _reject(event, "missing_levels", "trigger/invalidation required")

    entry = event.trigger.price_level
    stop = event.invalidation.price_level
    if entry <= 0 or stop <= 0:
        return _reject(event, "invalid_price", f"entry={entry} stop={stop}")

    if event.direction == "long" and stop >= entry:
        return _reject(event, "stop_wrong_side", f"long stop {stop} >= entry {entry}")
    if event.direction == "short" and stop <= entry:
        return _reject(event, "stop_wrong_side", f"short stop {stop} <= entry {entry}")

    hours = cfg.get("trading_hours_utc") or {}
    if hours.get("enabled") and not _within_trading_hours(hours):
        return _reject(event, "outside_trading_hours", str(hours))

    return None


def _reject(event: SetupEvent, reason: str, detail: str) -> Rejection:
    log.info("validator.reject", event_id=event.event_id, reason=reason, detail=detail)
    return Rejection(
        source_event_id=event.event_id,
        stage="validator",
        reason=reason,
        detail=detail,
    )


def _within_trading_hours(cfg: dict[str, object]) -> bool:
    now = datetime.utcnow().time()
    start = _parse_hhmm(str(cfg.get("start", "00:00")))
    end = _parse_hhmm(str(cfg.get("end", "23:59")))
    if start <= end:
        return start <= now <= end
    # window wraps midnight
    return now >= start or now <= end


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))
