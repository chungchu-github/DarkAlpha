"""Tests for the SQLite storage layer."""

import sqlite3
from pathlib import Path

from storage.db import get_db, init_db


def test_init_db_creates_file(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    init_db(db_file)
    assert db_file.exists()


def test_init_db_creates_all_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    init_db(db_file)

    conn = sqlite3.connect(db_file)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()

    assert "setup_events" in tables
    assert "execution_tickets" in tables
    assert "orders" in tables
    assert "trades" in tables
    assert "daily_snapshots" in tables
    assert "signal_journal" in tables
    assert "signal_outcomes" in tables
    assert "order_idempotency" in tables
    assert "reconciliation_runs" in tables


def test_init_db_idempotent(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    init_db(db_file)
    init_db(db_file)  # second call must not raise


def test_get_db_initializes_on_first_use(tmp_path: Path) -> None:
    db_file = tmp_path / "fresh.db"
    assert not db_file.exists()

    with get_db(db_file) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    assert "setup_events" in tables


def test_get_db_insert_and_query(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with get_db(db_file) as conn:
        conn.execute(
            "INSERT INTO setup_events VALUES (?,?,?,?,?,?)",
            ("ev1", "2026-01-01T00:00:00Z", "BTCUSDT-PERP", "active", "{}", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        row = conn.execute("SELECT event_id FROM setup_events WHERE event_id='ev1'").fetchone()

    assert row is not None
    assert row[0] == "ev1"


def test_get_db_wal_mode_enabled(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with get_db(db_file) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_get_db_applies_new_migrations_to_existing_file(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    sqlite3.connect(db_file).close()

    with get_db(db_file) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    assert "order_idempotency" in tables
    assert "reconciliation_runs" in tables
