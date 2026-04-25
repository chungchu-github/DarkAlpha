"""Tests for Phase 4 CLI commands: evaluate, report daily, report weekly."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.main import cli
from storage.db import get_db, init_db


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "cli.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    import reporting.weekly as w

    monkeypatch.setattr(w, "_REPORTS_DIR", tmp_path / "reports")
    init_db(db)
    return db


def test_evaluate_no_positions(runner: CliRunner, db_env: Path) -> None:
    result = runner.invoke(cli, ["evaluate"])
    assert result.exit_code == 0
    assert "No open positions" in result.output


def test_report_daily(runner: CliRunner, db_env: Path) -> None:
    yesterday = (datetime.now(tz=UTC).date() - timedelta(days=1)).isoformat()
    result = runner.invoke(cli, ["report", "daily", "--date", yesterday])
    assert result.exit_code == 0
    assert "daily snapshot written" in result.output


def test_report_weekly(runner: CliRunner, db_env: Path) -> None:
    with get_db(db_env) as conn:
        conn.execute(
            """INSERT INTO daily_snapshots
               (date, starting_equity, ending_equity, trade_count, win_count,
                loss_count, gross_pnl, fees, net_pnl, max_drawdown_intraday,
                mode, gate)
               VALUES (?, 10000, 10050, 1, 1, 0, 55, 5, 50, 0, 'shadow', 'gate1')""",
            (date(2026, 4, 15).isoformat(),),
        )
        conn.commit()

    result = runner.invoke(cli, ["report", "weekly", "--end", "2026-04-19"])
    assert result.exit_code == 0
    assert "weekly report written" in result.output
