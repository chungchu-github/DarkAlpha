"""SQLite connection factory and schema initialization."""

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_DEFAULT_DB_PATH = Path(os.getenv("DB_PATH", "data/shadow.db"))


def _db_path() -> Path:
    return Path(os.getenv("DB_PATH", str(_DEFAULT_DB_PATH)))


def init_db(path: Path | None = None) -> None:
    """Create database file and run all migrations if not already applied."""
    db_file = path or _db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_file) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        for migration in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            sql = migration.read_text()
            conn.executescript(sql)

        conn.commit()


@contextmanager
def get_db(path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding an open SQLite connection.

    Always applies idempotent migrations before opening the connection. This is
    important for long-lived local DB files created before newer Gate migrations
    existed.
    """
    db_file = path or _db_path()
    init_db(db_file)

    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
