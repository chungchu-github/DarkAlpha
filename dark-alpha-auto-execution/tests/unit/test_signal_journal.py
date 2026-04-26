"""Tests for signal journal persistence."""

from storage.db import get_db
from storage.signal_journal import record_signal


def test_record_signal_writes_journal_and_pending_outcomes(setup_event, db_path) -> None:
    event = setup_event.model_copy(
        update={
            "metadata": {
                "position_usdt": 500.0,
                "max_risk_usdt": 25.0,
                "leverage_suggest": 3,
                "ttl_minutes": 15,
                "invalid_condition": "invalid if stop is touched",
                "risk_level": "medium",
                "data_health": {"status": "fresh", "reason": "ok"},
            }
        }
    )

    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO setup_events
                (event_id, timestamp, symbol, setup_type, payload, received_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                event.event_id,
                event.timestamp,
                event.symbol,
                event.setup_type,
                event.model_dump_json(),
            ),
        )
        conn.commit()

    record_signal(event, db_path=db_path)

    with get_db(db_path) as conn:
        journal = conn.execute(
            "SELECT symbol, strategy, data_health_status, risk_level, invalid_condition FROM signal_journal WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        outcomes = conn.execute(
            "SELECT horizon, status FROM signal_outcomes WHERE event_id=? ORDER BY horizon",
            (event.event_id,),
        ).fetchall()

    assert journal is not None
    assert journal["symbol"] == "BTCUSDT-PERP"
    assert journal["strategy"] == "vol_breakout_card"
    assert journal["data_health_status"] == "fresh"
    assert journal["risk_level"] == "medium"
    assert journal["invalid_condition"] == "invalid if stop is touched"
    assert {row["horizon"] for row in outcomes} == {"5m", "15m", "1h", "4h"}
    assert {row["status"] for row in outcomes} == {"pending"}
