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
    monkeypatch.setattr(gate6_mod, "assert_live_mode_enabled", lambda *a, **kw: None)

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


# ----------------------------------------------------------------------
# Bug 6 fix tests — repair_local_flat_after_closeout backfills PnL
# ----------------------------------------------------------------------


def _seed_live_position(db_path: Path, *, direction: str, entry_price: float, qty: float) -> str:
    """Insert an open live position row for repair tests (with FK rows)."""
    from storage.db import get_db, init_db

    init_db(db_path)
    position_id = "pos-test-1"
    with get_db(db_path) as conn:
        conn.execute(
            """INSERT INTO setup_events
               (event_id, timestamp, symbol, setup_type, payload, received_at)
               VALUES ('evt-1','2026-05-05T00:00:00+00:00','ETHUSDT-PERP','active','{}',
                       datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO execution_tickets
               (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
               VALUES ('ticket-1','evt-1','accepted',0,'{}','2026-05-05T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO positions
               (position_id, ticket_id, symbol, direction, status,
                entry_price, quantity, filled_quantity, opened_at,
                shadow_mode)
               VALUES (?, 'ticket-1', 'ETHUSDT-PERP', ?, 'open',
                       ?, ?, ?, datetime('now'), 0)""",
            (position_id, direction, entry_price, qty, qty),
        )
        conn.commit()
    return position_id


def _fake_flatten_ack(client_order_id: str = "DACLOSE_TEST") -> object:
    from execution.binance_testnet_broker import LiveOrderAck

    return LiveOrderAck(
        client_order_id=client_order_id,
        exchange_order_id="ex-1",
        role="emergency_close",
        symbol="ETHUSDT",
        side="SELL",
        type="MARKET",
        status="FILLED",
        price=None,
        quantity=0.033,
    )


class FakeClientWithFlattenFill(FakeClient):
    """FakeClient whose query_order returns a usable flatten fill."""

    def __init__(self, *, avg_price: float, executed_qty: float) -> None:
        super().__init__()
        self._avg = avg_price
        self._exec = executed_qty

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:
        return {
            "avgPrice": str(self._avg),
            "executedQty": str(self._exec),
            "status": "FILLED",
        }


def test_repair_with_flatten_ack_backfills_long_pnl_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug 6: repair_local_flat_after_closeout with flatten_ack must
    populate exit_price + gross/fees/net PnL on the position row.
    Without this, daily-loss tracker can't see the realised loss and
    the -$5/day safety cap fails to engage on reconciler-flattened
    positions (canary 4 surface)."""
    from execution import gate6 as gate6_mod
    from execution.gate6 import repair_local_flat_after_closeout
    from storage.db import get_db

    monkeypatch.setattr(gate6_mod, "load_live_execution_config", _mainnet_config)
    monkeypatch.setattr(gate6_mod, "assert_live_mode_enabled", lambda *a, **kw: None)

    db = tmp_path / "repair.db"
    monkeypatch.setenv("DB_PATH", str(db))
    _seed_live_position(db, direction="long", entry_price=2382.38, qty=0.033)

    fake = FakeClientWithFlattenFill(avg_price=2390.50, executed_qty=0.033)
    flatten_ack = _fake_flatten_ack()

    result = repair_local_flat_after_closeout(
        "ETHUSDT-PERP",
        yes=True,
        client=fake,
        flatten_ack=flatten_ack,
    )

    assert result.closed_positions == 1
    with get_db(db) as conn:
        row = conn.execute(
            "SELECT status, exit_reason, exit_price, gross_pnl_usd, "
            "fees_usd, net_pnl_usd FROM positions"
        ).fetchone()
    assert row["status"] == "closed"
    assert row["exit_reason"] == "manual_flatten_reconciled"
    assert row["exit_price"] == pytest.approx(2390.50)
    expected_gross = (2390.50 - 2382.38) * 0.033
    assert row["gross_pnl_usd"] == pytest.approx(expected_gross, abs=1e-6)
    expected_fees = 0.0005 * (2382.38 + 2390.50) * 0.033
    assert row["fees_usd"] == pytest.approx(expected_fees, abs=1e-6)
    assert row["net_pnl_usd"] == pytest.approx(expected_gross - expected_fees, abs=1e-6)


def test_repair_with_flatten_ack_backfills_short_pnl_with_correct_sign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHORT direction: PnL = (entry - exit) * qty (opposite of long)."""
    from execution import gate6 as gate6_mod
    from execution.gate6 import repair_local_flat_after_closeout
    from storage.db import get_db

    monkeypatch.setattr(gate6_mod, "load_live_execution_config", _mainnet_config)
    monkeypatch.setattr(gate6_mod, "assert_live_mode_enabled", lambda *a, **kw: None)

    db = tmp_path / "repair.db"
    monkeypatch.setenv("DB_PATH", str(db))
    _seed_live_position(db, direction="short", entry_price=2400.0, qty=0.05)

    # Short opened at 2400, flattened at 2380 → SHORT WIN
    fake = FakeClientWithFlattenFill(avg_price=2380.0, executed_qty=0.05)
    repair_local_flat_after_closeout(
        "ETHUSDT-PERP",
        yes=True,
        client=fake,
        flatten_ack=_fake_flatten_ack(),
    )

    with get_db(db) as conn:
        row = conn.execute("SELECT gross_pnl_usd, net_pnl_usd FROM positions").fetchone()
    expected_gross = (2400.0 - 2380.0) * 0.05  # +$1.00
    assert row["gross_pnl_usd"] == pytest.approx(expected_gross, abs=1e-6)
    assert row["net_pnl_usd"] < expected_gross  # fees deducted


def test_repair_without_flatten_ack_falls_back_to_legacy_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backward compat: standalone repair-flat call (no flatten_ack)
    still marks closed but leaves exit_price / PnL blank, exactly as
    before Bug 6 fix."""
    from execution import gate6 as gate6_mod
    from execution.gate6 import repair_local_flat_after_closeout
    from storage.db import get_db

    monkeypatch.setattr(gate6_mod, "load_live_execution_config", _mainnet_config)
    monkeypatch.setattr(gate6_mod, "assert_live_mode_enabled", lambda *a, **kw: None)

    db = tmp_path / "repair.db"
    monkeypatch.setenv("DB_PATH", str(db))
    _seed_live_position(db, direction="long", entry_price=2382.38, qty=0.033)

    repair_local_flat_after_closeout("ETHUSDT-PERP", yes=True, client=FakeClient())

    with get_db(db) as conn:
        row = conn.execute(
            "SELECT status, exit_price, gross_pnl_usd, net_pnl_usd FROM positions"
        ).fetchone()
    assert row["status"] == "closed"
    assert row["exit_price"] is None
    assert row["gross_pnl_usd"] is None
    assert row["net_pnl_usd"] is None


def test_repair_with_flatten_ack_handles_zero_fill_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If query_order returns empty/zero data (e.g. cancelled before
    fill), repair must NOT compute bogus PnL — falls back to legacy
    no-PnL closure."""
    from execution import gate6 as gate6_mod
    from execution.gate6 import repair_local_flat_after_closeout
    from storage.db import get_db

    monkeypatch.setattr(gate6_mod, "load_live_execution_config", _mainnet_config)
    monkeypatch.setattr(gate6_mod, "assert_live_mode_enabled", lambda *a, **kw: None)

    db = tmp_path / "repair.db"
    monkeypatch.setenv("DB_PATH", str(db))
    _seed_live_position(db, direction="long", entry_price=2382.38, qty=0.033)

    # query_order returns zero — no usable fill
    fake = FakeClientWithFlattenFill(avg_price=0.0, executed_qty=0.0)
    repair_local_flat_after_closeout(
        "ETHUSDT-PERP",
        yes=True,
        client=fake,
        flatten_ack=_fake_flatten_ack(),
    )

    with get_db(db) as conn:
        row = conn.execute("SELECT exit_price, gross_pnl_usd FROM positions").fetchone()
    assert row["exit_price"] is None
    assert row["gross_pnl_usd"] is None
