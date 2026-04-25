"""Unit tests for the audit trail module."""

import json
from pathlib import Path

import pytest

from safety.audit import (
    CIRCUIT_BREAKER_CLEARED,
    CIRCUIT_BREAKER_TRIPPED,
    KILL_SWITCH_ACTIVATED,
    KILL_SWITCH_CLEARED,
    SIGNAL_ACCEPTED,
    SIGNAL_RECEIVED,
    SIGNAL_REJECTED,
    log_event,
)
from storage.db import get_db, init_db


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "audit_test.db"
    init_db(p)
    return p


# ------------------------------------------------------------------
# Basic write
# ------------------------------------------------------------------


def test_log_event_writes_row(db_path: Path) -> None:
    log_event(
        SIGNAL_RECEIVED,
        source="test",
        decision="received",
        db_path=db_path,
    )
    with get_db(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert count == 1


def test_log_event_all_fields_stored(db_path: Path) -> None:
    log_event(
        SIGNAL_REJECTED,
        source="receiver",
        decision="reject",
        reason="kill_switch_active",
        event_id="evt-001",
        metadata={"foo": "bar"},
        db_path=db_path,
    )
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM audit_log LIMIT 1").fetchone()

    assert row["event_type"] == SIGNAL_REJECTED
    assert row["source"] == "receiver"
    assert row["decision"] == "reject"
    assert row["reason"] == "kill_switch_active"
    assert row["event_id"] == "evt-001"
    assert json.loads(row["metadata"]) == {"foo": "bar"}
    assert row["created_at"] is not None


def test_log_event_without_optional_fields(db_path: Path) -> None:
    log_event(SIGNAL_ACCEPTED, source="receiver", decision="accept", db_path=db_path)
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM audit_log LIMIT 1").fetchone()
    assert row["event_type"] == SIGNAL_ACCEPTED
    assert row["event_id"] is None
    assert row["metadata"] is None


# ------------------------------------------------------------------
# Multiple writes
# ------------------------------------------------------------------


def test_multiple_entries_appended(db_path: Path) -> None:
    for event_type in [SIGNAL_RECEIVED, SIGNAL_ACCEPTED, KILL_SWITCH_ACTIVATED]:
        log_event(event_type, source="test", decision="x", db_path=db_path)

    with get_db(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert count == 3


# ------------------------------------------------------------------
# Failure resilience — audit errors must not crash callers
# ------------------------------------------------------------------


def test_log_event_survives_db_error(tmp_path: Path) -> None:
    bad_path = tmp_path / "nonexistent_dir" / "bad.db"
    log_event(SIGNAL_RECEIVED, source="test", decision="x", db_path=bad_path)
    # No exception raised — audit failures are swallowed


# ------------------------------------------------------------------
# Event type constants
# ------------------------------------------------------------------


def test_event_type_constants_are_strings() -> None:
    assert isinstance(SIGNAL_RECEIVED, str)
    assert isinstance(SIGNAL_ACCEPTED, str)
    assert isinstance(SIGNAL_REJECTED, str)
    assert isinstance(KILL_SWITCH_ACTIVATED, str)
    assert isinstance(KILL_SWITCH_CLEARED, str)
    assert isinstance(CIRCUIT_BREAKER_TRIPPED, str)
    assert isinstance(CIRCUIT_BREAKER_CLEARED, str)
