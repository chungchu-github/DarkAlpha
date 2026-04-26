"""Tests for Gate 6 mainnet micro-live helpers."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from execution.gate6 import (
    Gate6Error,
    Gate6Preflight,
    _validate_canary_conditional_triggers,
    run_gate6_closeout,
    write_gate6_authorization,
)
from execution.live_reconciliation import ReconciliationResult
from execution.live_safety import LiveExecutionConfig
from safety.kill_switch import KillSwitch


class FakeClient:
    def __init__(self) -> None:
        self.positions: list[Mapping[str, Any]] = [
            {
                "positionAmt": "0",
                "leverage": "1",
                "marginType": "isolated",
                "positionSide": "BOTH",
            }
        ]
        self.orders: list[Mapping[str, Any]] = []
        self.algo_orders: list[Mapping[str, Any]] = []

    def set_leverage(self, symbol: str, leverage: int) -> Mapping[str, Any]:
        return {}

    def position_risk(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.positions

    def open_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.orders

    def open_algo_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.algo_orders

    def new_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def new_algo_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:
        return {}

    def query_algo_order(self, symbol: str, client_algo_id: str) -> Mapping[str, Any]:
        return {}

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:
        return {"regular": "ok"}

    def cancel_all_open_algo_orders(self, symbol: str) -> Mapping[str, Any]:
        return {"algo": "ok"}


class FakeBroker:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.flattened: list[str] = []

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:
        self.cancelled.append(symbol)
        return {"regular": {"code": 200}, "algo": {"code": 200}}

    def emergency_close_symbol(self, symbol: str) -> object | None:
        self.flattened.append(symbol)
        return None


class FakeSync:
    def sync_symbol(self, symbol: str) -> list[object]:
        return []


class FakeReconciler:
    def run(self, symbols: list[str]) -> ReconciliationResult:
        return ReconciliationResult(run_id="run-1", status="ok", symbols=[])


def _mainnet_config() -> LiveExecutionConfig:
    return LiveExecutionConfig(
        mode="live",
        environment="mainnet",
        allow_mainnet=True,
        require_gate_authorization=False,
        gate_authorization_file="docs/gate-6-authorization.md",
        micro_live={
            "enabled": True,
            "allowed_symbols": ["ETHUSDT-PERP"],
            "max_notional_usd": 10,
            "max_leverage": 1,
            "max_daily_loss_usd": 5,
            "max_concurrent_positions": 1,
            "exercise_window_start": "2026-01-01T00:00:00+00:00",
            "exercise_window_end": "2026-12-31T23:59:59+00:00",
        },
    )


@pytest.fixture(autouse=True)
def mainnet_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINANCE_FUTURES_MAINNET_API_KEY", "key")
    monkeypatch.setenv("BINANCE_FUTURES_MAINNET_API_SECRET", "secret")


def test_gate6_preflight_passes_clean_account(tmp_path: Path) -> None:
    result = Gate6Preflight(
        client=FakeClient(),
        config=_mainnet_config(),
        kill_switch=KillSwitch(sentinel_path=tmp_path / "kill"),
    ).run()

    assert result.status == "ok"
    assert result.symbols[0].symbol == "ETHUSDT-PERP"
    assert result.symbols[0].open_algo_orders == 0


def test_gate6_preflight_blocks_open_algo_orders(tmp_path: Path) -> None:
    client = FakeClient()
    client.algo_orders = [{"clientAlgoId": "DAOPEN"}]

    with pytest.raises(Gate6Error, match="gate6_account_not_clean"):
        Gate6Preflight(
            client=client,
            config=_mainnet_config(),
            kill_switch=KillSwitch(sentinel_path=tmp_path / "kill"),
        ).run()


def test_write_gate6_authorization(tmp_path: Path) -> None:
    path = write_gate6_authorization(
        symbol="ETHUSDT-PERP",
        max_notional_usd=10,
        max_leverage=1,
        max_daily_loss_usd=5,
        window_start="2026-04-26T08:00:00+00:00",
        window_end="2026-04-26T08:30:00+00:00",
        strategy_scope="manual_test_signal",
        directions="long",
        auto_flatten=True,
        operator="test",
        output=tmp_path / "gate-6-authorization.md",
    )

    text = path.read_text()
    assert "Authorized symbol: `ETHUSDT-PERP`" in text
    assert "gate_authorization_file: docs/gate-6-authorization.md" in text


def test_gate6_closeout_requires_yes() -> None:
    with pytest.raises(Gate6Error, match="requires_yes"):
        run_gate6_closeout("ETHUSDT-PERP", yes=False)


def test_gate6_rejects_long_take_profit_that_would_immediately_trigger() -> None:
    with pytest.raises(Gate6Error, match="take_profit_would_immediately_trigger"):
        _validate_canary_conditional_triggers(
            direction="long",
            mark_price=100.0,
            stop_price=98.0,
            take_profit_price=99.8,
        )


def test_gate6_rejects_short_take_profit_that_would_immediately_trigger() -> None:
    with pytest.raises(Gate6Error, match="take_profit_would_immediately_trigger"):
        _validate_canary_conditional_triggers(
            direction="short",
            mark_price=100.0,
            stop_price=102.0,
            take_profit_price=100.2,
        )


def test_gate6_closeout_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from execution import gate6 as gate6_mod

    monkeypatch.setattr(gate6_mod, "load_live_execution_config", _mainnet_config)
    monkeypatch.setattr(gate6_mod, "assert_live_mode_enabled", lambda: None)

    result = run_gate6_closeout(
        "ETHUSDT-PERP",
        yes=True,
        broker=FakeBroker(),
        sync=FakeSync(),
        reconciler=FakeReconciler(),
        reports_dir=tmp_path,
    )

    assert result.reconciliation.status == "ok"
    assert result.report_path.exists()
