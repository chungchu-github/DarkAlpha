"""Signal validator — first filter after ingestion.

Checks (spec Section 4.3):
  - setup_type must be 'active'
  - direction must be long/short (not None)
  - signal timestamp + ttl_minutes (Task 9 — fail closed on stale or missing)
  - ranking_score >= min_ranking_score
  - regime not in blocked_regimes
  - trigger / invalidation prices present and on the correct side
  - trading_hours_utc (optional, disabled by default for crypto)
"""

from datetime import UTC, datetime, time, timedelta
from typing import Any

import structlog

from signal_adapter.schemas import SetupEvent

from .config import validator_config
from .schemas import Rejection

log = structlog.get_logger(__name__)

_DEFAULT_MAX_FUTURE_SKEW_SECONDS = 30


def validate(event: SetupEvent) -> Rejection | None:
    """Return None if the event should proceed, or a Rejection if blocked."""
    cfg = validator_config()
    min_score = float(cfg.get("min_ranking_score", 7.0))
    blocked = set(cfg.get("blocked_regimes", []))

    if event.setup_type != "active":
        return _reject(event, "not_active", f"setup_type={event.setup_type}")

    if event.direction not in {"long", "short"}:
        return _reject(event, "no_direction", f"direction={event.direction}")

    freshness = _check_signal_freshness(event, cfg)
    if freshness is not None:
        return freshness

    if event.ranking_score < min_score:
        return _reject(
            event,
            "low_ranking_score",
            f"{event.ranking_score:.2f} < {min_score:.2f}",
        )

    if event.regime in blocked:
        return _reject(event, "blocked_regime", f"regime={event.regime}")

    data_health = event.metadata.get("data_health")
    if isinstance(data_health, dict):
        health_status = str(data_health.get("status") or "").lower()
        health_reason = str(data_health.get("reason") or "unknown")
        if health_status and health_status != "fresh":
            return _reject(event, "data_unhealthy", health_reason)

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


def _check_signal_freshness(event: SetupEvent, cfg: dict[str, Any]) -> Rejection | None:
    """Reject signals that lack a parseable timestamp or whose TTL has expired.

    Task 9 — stale signal guard. Translator preserves ``ttl_minutes`` and
    ``timestamp`` from the upstream ProposalCard; validator is the single point
    that enforces freshness before downstream sizing/risk gates run.
    """
    ts = _parse_iso_timestamp(event.timestamp)
    if ts is None:
        return _reject(
            event,
            "invalid_signal_timestamp",
            f"timestamp={event.timestamp!r}",
        )

    ttl_raw = event.metadata.get("ttl_minutes")
    ttl_minutes = _coerce_positive_int(ttl_raw)
    if ttl_minutes is None:
        return _reject(event, "missing_signal_ttl", f"ttl_minutes={ttl_raw!r}")

    now = datetime.now(tz=UTC)
    max_future_skew = timedelta(
        seconds=int(cfg.get("max_signal_clock_skew_seconds", _DEFAULT_MAX_FUTURE_SKEW_SECONDS))
    )
    if ts > now + max_future_skew:
        return _reject(
            event,
            "signal_timestamp_in_future",
            f"timestamp={event.timestamp} now={now.isoformat()}",
        )

    expiry = ts + timedelta(minutes=ttl_minutes)
    if now > expiry:
        return _reject(
            event,
            "signal_expired",
            f"expiry={expiry.isoformat()} now={now.isoformat()}",
        )

    return None


def _parse_iso_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Reject naive timestamps — we can't safely compare them to UTC.
        return None
    return parsed.astimezone(UTC)


def _coerce_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):  # bool is a subclass of int — explicitly disallow
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
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
