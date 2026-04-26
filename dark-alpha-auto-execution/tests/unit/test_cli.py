"""Unit tests for the CLI commands."""

from pathlib import Path
from types import SimpleNamespace

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


def test_status_shows_live_execution_section(runner: CliRunner) -> None:
    """The status command must surface live mode / environment / armed state."""
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "Live execution" in result.output
    assert "mode" in result.output
    assert "environment" in result.output
    assert "allow_mainnet" in result.output
    assert "mainnet live armed" in result.output
    # Default committed config must show NOT armed.
    assert "🔴 YES" not in result.output


def test_status_shows_armed_when_mainnet_preflight_passes(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When live config is in valid mainnet armed state, status must scream 🔴 YES."""
    import execution.live_safety as live_safety

    monkeypatch.setattr(
        live_safety,
        "load_live_execution_config",
        lambda: live_safety.LiveExecutionConfig(
            mode="live",
            environment="mainnet",
            allow_mainnet=True,
            require_gate_authorization=False,
            gate_authorization_file="docs/gate-6-authorization.md",
            micro_live={
                "enabled": True,
                "allowed_symbols": ["BTCUSDT-PERP"],
                "max_notional_usd": 20,
                "max_leverage": 2,
                "max_daily_loss_usd": 5,
                "max_concurrent_positions": 1,
                "exercise_window_start": "2026-01-01T00:00:00+00:00",
                "exercise_window_end": "2026-12-31T23:59:59+00:00",
            },
        ),
    )
    monkeypatch.setattr(live_safety, "assert_mainnet_readiness", lambda *_a, **_k: None)

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "mainnet live armed : 🔴 YES" in result.output


def test_status_shows_blocked_reason_when_mainnet_misconfigured(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If mode=live+mainnet but preflight fails, status must show blocked reason."""
    import execution.live_safety as live_safety

    monkeypatch.setattr(
        live_safety,
        "load_live_execution_config",
        lambda: live_safety.LiveExecutionConfig(
            mode="live",
            environment="mainnet",
            allow_mainnet=True,
            require_gate_authorization=False,
            gate_authorization_file="docs/gate-6-authorization.md",
            micro_live={"enabled": False},
        ),
    )

    def _raise(*_a: object, **_k: object) -> None:
        raise live_safety.LivePreflightError("mainnet_micro_live_not_enabled")

    monkeypatch.setattr(live_safety, "assert_mainnet_readiness", _raise)

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "mainnet live armed : 🟢 no" in result.output
    assert "mainnet_micro_live_not_enabled" in result.output


def test_flatten_exits_with_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["flatten"])
    assert result.exit_code != 0
    assert "Phase 5" in result.output


def test_reconcile_live_prints_ok(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    import execution.live_reconciliation as recon
    import execution.live_safety as safety

    monkeypatch.setattr(safety, "assert_live_mode_enabled", lambda: None)

    class FakeReconciler:
        def run(self, symbols: list[str]) -> object:
            assert symbols == ["BTCUSDT-PERP"]
            return _result("ok")

        def run_for_local_symbols(self) -> object:
            return _result("ok")

    monkeypatch.setattr(recon, "LiveReconciler", FakeReconciler)

    result = runner.invoke(cli, ["reconcile-live", "--symbol", "BTCUSDT-PERP"])

    assert result.exit_code == 0
    assert "status=ok" in result.output


def test_reconcile_live_exits_nonzero_on_mismatch(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import execution.live_reconciliation as recon
    import execution.live_safety as safety

    monkeypatch.setattr(safety, "assert_live_mode_enabled", lambda: None)

    class FakeReconciler:
        def run_for_local_symbols(self) -> object:
            return _result("mismatch", ["BTCUSDT-PERP:unexpected_exchange_orders=DAUNKNOWN"])

    monkeypatch.setattr(recon, "LiveReconciler", FakeReconciler)

    result = runner.invoke(cli, ["reconcile-live"])

    assert result.exit_code == 3
    assert "mismatch" in result.output


def test_sync_live_orders_prints_status(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    import execution.live_order_sync as sync_mod
    import execution.live_safety as safety

    monkeypatch.setattr(safety, "assert_live_mode_enabled", lambda: None)

    class FakeSync:
        def sync_all(self) -> list[object]:
            return [
                SimpleNamespace(
                    client_order_id="DAENB1",
                    exchange_status="FILLED",
                    local_status="filled",
                    filled_quantity=0.01,
                    average_price=100.0,
                )
            ]

        def sync_symbol(self, symbol: str) -> list[object]:
            return self.sync_all()

    monkeypatch.setattr(sync_mod, "LiveOrderStatusSync", FakeSync)

    result = runner.invoke(cli, ["sync-live-orders"])

    assert result.exit_code == 0
    assert "DAENB1 exchange=FILLED local=filled" in result.output


def test_gate2_report_command_prints_path(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    import reporting.gate2 as gate2

    monkeypatch.setattr(gate2, "write_report", lambda **kwargs: Path("reports/gate2-test.md"))

    result = runner.invoke(cli, ["gate2-test", "report", "--ticket-id", "t1"])

    assert result.exit_code == 0
    assert "gate2 report written" in result.output


def test_gate2_bracket_dry_run_prints_payload(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import execution.gate2_test as gate2_test_mod

    class FakeBuilder:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build_bracket_payload(self, **kwargs) -> object:
            return SimpleNamespace(
                mark_price=100.0,
                payload={"symbol": "ETHUSDT", "trace_id": "trace-1"},
            )

    monkeypatch.setattr(gate2_test_mod, "Gate2TestBuilder", FakeBuilder)

    result = runner.invoke(cli, ["gate2-test", "bracket", "--trace-id", "trace-1"])

    assert result.exit_code == 0
    assert "dry_run=true" in result.output
    assert '"trace_id": "trace-1"' in result.output


def _result(status: str, mismatches: list[str] | None = None) -> object:
    return SimpleNamespace(
        run_id="run-1",
        status=status,
        symbols=[
            SimpleNamespace(
                symbol="BTCUSDT-PERP",
                mismatches=mismatches or [],
            )
        ],
    )
