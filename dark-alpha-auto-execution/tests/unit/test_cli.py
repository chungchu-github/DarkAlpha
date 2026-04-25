"""Unit tests for the CLI commands."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.main import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def sentinel(tmp_path: Path) -> Path:
    return tmp_path / "test-kill"


def test_halt_creates_sentinel(runner: CliRunner, sentinel: Path) -> None:
    result = runner.invoke(cli, ["halt", "--sentinel", str(sentinel), "--reason", "test"])
    assert result.exit_code == 0
    assert sentinel.exists()
    assert "ACTIVATED" in result.output


def test_halt_output_contains_sentinel_path(runner: CliRunner, sentinel: Path) -> None:
    result = runner.invoke(cli, ["halt", "--sentinel", str(sentinel)])
    assert str(sentinel) in result.output


def test_resume_when_not_active_prints_message(runner: CliRunner, sentinel: Path) -> None:
    result = runner.invoke(cli, ["resume", "--sentinel", str(sentinel)])
    assert result.exit_code == 0
    assert "not active" in result.output.lower()


def test_resume_when_active_requires_confirmation(runner: CliRunner, sentinel: Path) -> None:
    sentinel.touch()
    # Provide "n" to the confirmation prompt → aborts
    result = runner.invoke(cli, ["resume", "--sentinel", str(sentinel)], input="n\n")
    assert result.exit_code != 0


def test_resume_when_active_clears_on_confirm(runner: CliRunner, sentinel: Path) -> None:
    sentinel.touch()
    result = runner.invoke(cli, ["resume", "--sentinel", str(sentinel)], input="y\n")
    assert result.exit_code == 0
    assert not sentinel.exists()
    assert "cleared" in result.output.lower()


def test_status_runs_without_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "Kill switch" in result.output


def test_status_shows_circuit_breakers(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["status"])
    assert "Circuit breakers" in result.output


def test_flatten_exits_with_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["flatten"])
    assert result.exit_code != 0
    assert "Phase 5" in result.output
