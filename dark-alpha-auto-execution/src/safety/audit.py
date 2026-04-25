"""Audit trail — every decision leaves a record.

Every signal received, accepted, rejected, kill switch change, and
circuit breaker event is written here. Immutable append-only log.
"""

import json
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Canonical event_type values
SIGNAL_RECEIVED = "signal_received"
SIGNAL_ACCEPTED = "signal_accepted"
SIGNAL_REJECTED = "signal_rejected"
KILL_SWITCH_ACTIVATED = "kill_switch_activated"
KILL_SWITCH_CLEARED = "kill_switch_cleared"
CIRCUIT_BREAKER_TRIPPED = "circuit_breaker_tripped"
CIRCUIT_BREAKER_CLEARED = "circuit_breaker_cleared"


def log_event(
    event_type: str,
    source: str,
    decision: str,
    reason: str = "",
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> None:
    """Append one audit entry to the audit_log table.

    Never raises — audit failures must not interrupt the hot path.
    """
    try:
        from storage.db import get_db

        with get_db(db_path) as conn:
            conn.execute(
                """
                INSERT INTO audit_log
                    (event_type, event_id, source, decision, reason, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    event_id,
                    source,
                    decision,
                    reason,
                    json.dumps(metadata) if metadata else None,
                ),
            )
            conn.commit()

        log.debug(
            "audit.logged",
            event_type=event_type,
            source=source,
            decision=decision,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("audit.write_failed", event_type=event_type, error=str(exc))
