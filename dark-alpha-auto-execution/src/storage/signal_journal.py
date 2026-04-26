"""Signal journal persistence.

The journal is the audit bridge between raw signal intake and strategy results.
It records the accepted setup plus pending post-signal horizons so later
reporting can measure whether a strategy had follow-through after emission.
"""

from pathlib import Path
from typing import Any

from signal_adapter.schemas import SetupEvent

from .db import get_db

_HORIZONS = ("5m", "15m", "1h", "4h")


def record_signal(event: SetupEvent, db_path: Path | None = None) -> None:
    trigger = event.trigger
    invalidation = event.invalidation
    metadata = event.metadata
    data_health = metadata.get("data_health")
    if not isinstance(data_health, dict):
        data_health = {}

    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO signal_journal
                (event_id, timestamp, symbol, strategy, direction, ranking_score,
                 entry_price, stop_price, take_profit_price, position_usdt,
                 max_risk_usdt, leverage_suggest, ttl_minutes,
                 invalid_condition, risk_level,
                 data_health_status, data_health_reason, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.timestamp,
                event.symbol,
                event.regime,
                event.direction,
                event.ranking_score,
                trigger.price_level if trigger else None,
                invalidation.price_level if invalidation else None,
                _float_or_none(metadata.get("take_profit_price")),
                _float_or_none(metadata.get("position_usdt")),
                _float_or_none(metadata.get("max_risk_usdt")),
                _float_or_none(metadata.get("leverage_suggest")),
                _int_or_none(metadata.get("ttl_minutes")),
                str(metadata.get("invalid_condition") or ""),
                str(metadata.get("risk_level") or "unknown"),
                str(data_health.get("status") or "unknown"),
                str(data_health.get("reason") or "unknown"),
                event.model_dump_json(),
            ),
        )
        for horizon in _HORIZONS:
            conn.execute(
                """
                INSERT OR IGNORE INTO signal_outcomes (event_id, horizon)
                VALUES (?, ?)
                """,
                (event.event_id, horizon),
            )
        conn.commit()


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return int(parsed)
