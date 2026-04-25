"""Tests for Phase 8 CLI entries: telegram, doctor."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.main import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_doctor_reports_env(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("CONFIG_DIR", "config")  # real configs exist in repo
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code == 0
    assert "Dark Alpha doctor" in result.output
    assert "TELEGRAM_BOT_TOKEN" in result.output


def test_telegram_command_requires_token(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    result = runner.invoke(cli, ["telegram"])
    assert result.exit_code == 2
    assert "TELEGRAM_BOT_TOKEN" in result.output


def test_telegram_command_requires_admin(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_IDS", raising=False)
    result = runner.invoke(cli, ["telegram"])
    assert result.exit_code == 2
    assert "admin chat IDs" in result.output
