"""Tests for Gate 2 live startup reconciliation."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from execution.live_reconciliation import LiveReconciler
from safety.kill_switch import KillSwitch
from storage.db import get_db, init_db
from strategy.schemas import ExecutionTicket, PlannedOrder


class FakeClient:
    def __init__(self) -> None:
        self.open: list[Mapping[str, Any]] = [{"clientOrderId": "DAENBTICKET1"}]
        self.open_algo: list[Mapping[str, Any]] = []
        self.positions: list[Mapping[str, Any]] = [{"positionAmt": "0"}]

    def set_leverage(self, symbol: str, leverage: int) -> Mapping[str, Any]:
        return {}

    def position_risk(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.positions

    def open_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.open

    def open_algo_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.open_algo

    def new_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def new_algo_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:
        return {
            "symbol": "BTCUSDT",
            "clientOrderId": client_order_id,
            "status": "NEW",
            "executedQty": "0",
        }

    def query_algo_order(self, symbol: str, client_algo_id: str) -> Mapping[str, Any]:
        return {
            "symbol": "BTCUSDT",
            "clientAlgoId": client_algo_id,
            "algoStatus": "NEW",
            "executedQty": "0",
        }

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:
        return {}

    def cancel_all_open_algo_orders(self, symbol: str) -> Mapping[str, Any]:
        return {}


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "reconcile.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO setup_events
               (event_id, timestamp, symbol, setup_type, payload, received_at)
               VALUES ('evt-1','2026-04-18T00:00:00+00:00','BTCUSDT-PERP','active','{}',
                       datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO execution_tickets
               (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
               VALUES ('ticket-1','evt-1','accepted',0,?,'2026-04-18T00:00:00+00:00')""",
            (_ticket().model_dump_json(),),
        )
        conn.execute(
            """INSERT INTO order_idempotency
               (client_order_id, ticket_id, order_role, symbol, side, quantity, price, status)
               VALUES ('DAENBTICKET1','ticket-1','entry','BTCUSDT-PERP','buy',0.01,100,'submitted')"""
        )
        conn.execute(
            """INSERT INTO orders
               (order_id, ticket_id, exchange_order_id, side, type, symbol, price,
                quantity, status, submitted_at)
               VALUES ('DAENBTICKET1','ticket-1','ex-1','buy','LIMIT','BTCUSDT',100,
                       0.01,'new',datetime('now'))"""
        )
        conn.commit()
    return db


def _ticket() -> ExecutionTicket:
    return ExecutionTicket(
        ticket_id="ticket-1",
        source_event_id="evt-1",
        symbol="BTCUSDT-PERP",
        direction="long",
        regime="vol_breakout_card",
        ranking_score=8.0,
        shadow_mode=False,
        gate="gate2",
        entry_price=100.0,
        stop_price=99.0,
        take_profit_price=102.0,
        quantity=0.01,
        notional_usd=1.0,
        leverage=2.0,
        risk_usd=0.01,
        orders=[
            PlannedOrder(
                role="entry",
                side="buy",
                type="limit",
                symbol="BTCUSDT-PERP",
                price=100.0,
                quantity=0.01,
            ),
            PlannedOrder(
                role="stop",
                side="sell",
                type="stop_market",
                symbol="BTCUSDT-PERP",
                price=99.0,
                quantity=0.01,
                reduce_only=True,
            ),
            PlannedOrder(
                role="take_profit",
                side="sell",
                type="limit",
                symbol="BTCUSDT-PERP",
                price=102.0,
                quantity=0.01,
                reduce_only=True,
            ),
        ],
        created_at="2026-04-18T00:00:00+00:00",
    )


def _kill_switch(tmp_path: Path) -> KillSwitch:
    return KillSwitch(sentinel_path=tmp_path / "kill")


def test_reconciliation_ok_when_local_and_exchange_orders_match(
    ready_db: Path,
    tmp_path: Path,
) -> None:
    ks = _kill_switch(tmp_path)
    result = LiveReconciler(client=FakeClient(), db_path=ready_db, kill_switch=ks).run(
        ["BTCUSDT-PERP"]
    )

    assert result.status == "ok"
    assert not ks.is_active()
    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status FROM reconciliation_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()
    assert row["status"] == "ok"


def test_reconciliation_halts_on_unexpected_exchange_order(
    ready_db: Path,
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.open = [{"clientOrderId": "DAUNKNOWN"}]
    ks = _kill_switch(tmp_path)

    result = LiveReconciler(client=client, db_path=ready_db, kill_switch=ks).run(["BTCUSDT-PERP"])

    assert result.status == "mismatch"
    assert ks.is_active()
    assert "unexpected_exchange_orders=DAUNKNOWN" in result.mismatches[0]


def test_reconciliation_halts_on_exchange_position_without_local_position(
    ready_db: Path,
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.positions = [{"positionAmt": "0.02"}]
    ks = _kill_switch(tmp_path)

    result = LiveReconciler(client=client, db_path=ready_db, kill_switch=ks).run(["BTCUSDT-PERP"])

    assert result.status == "mismatch"
    assert ks.is_active()
    assert any("exchange_position_without_local_position" in item for item in result.mismatches)


def test_run_for_local_symbols_uses_active_local_order_symbols(
    ready_db: Path,
    tmp_path: Path,
) -> None:
    reconciler = LiveReconciler(
        client=FakeClient(), db_path=ready_db, kill_switch=_kill_switch(tmp_path)
    )

    assert reconciler.local_symbols() == ["BTCUSDT-PERP"]
    assert reconciler.run_for_local_symbols().status == "ok"


# ---------------------------------------------------------------------------
# Stage B audit P0 — malformed positionAmt must NOT silently report 'ok'
# ---------------------------------------------------------------------------


def test_malformed_position_amount_marks_run_failed_and_halts(
    ready_db: Path,
    tmp_path: Path,
) -> None:
    """If Binance returns garbage in positionAmt, reconciler must fail closed
    (status='failed' + kill switch activated). Previously ``_float`` swallowed
    the error and returned 0.0, producing a false 'ok' that would hide a real
    exchange position from mismatch detection."""
    client = FakeClient()
    client.positions = [{"positionAmt": "not-a-number"}]
    ks = _kill_switch(tmp_path)

    reconciler = LiveReconciler(client=client, db_path=ready_db, kill_switch=ks)

    with pytest.raises(Exception):  # noqa: B017,PT011 — re-raised after kill switch
        reconciler.run(["BTCUSDT-PERP"])

    assert ks.is_active(), "kill switch must activate on reconciliation data error"
    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status FROM reconciliation_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row["status"] == "failed"


def test_missing_position_amount_field_treated_as_zero(
    ready_db: Path,
    tmp_path: Path,
) -> None:
    """A row with no positionAmt key is legitimately an empty/flat position;
    that path must remain lenient (not trigger fail-closed)."""
    client = FakeClient()
    client.positions = [{"otherField": "ignored"}]  # no positionAmt key
    ks = _kill_switch(tmp_path)

    reconciler = LiveReconciler(client=client, db_path=ready_db, kill_switch=ks)
    result = reconciler.run(["BTCUSDT-PERP"])

    assert result.status == "ok"
    assert not ks.is_active()


# ---------------------------------------------------------------------------
# Incident 2026-04-26 — reconcile must invoke LiveEventGuard so a fill that
# bypassed the user-stream path (REST-only sync, missed event, etc.) cannot
# leave a position unprotected just because local↔exchange counts match.
# ---------------------------------------------------------------------------


def _insert_open_live_position(db: Path, *, ticket_id: str, symbol: str = "BTCUSDT-PERP") -> None:
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO positions
               (position_id, ticket_id, symbol, direction, status, shadow_mode,
                quantity, filled_quantity, entry_price, opened_at)
               VALUES (?, ?, ?, 'long', 'open', 0, 0.01, 0.01, 100, datetime('now'))""",
            (f"pos-{ticket_id}", ticket_id, symbol),
        )
        conn.commit()


def _insert_protective_orders(
    db: Path, *, ticket_id: str, symbol: str = "BTCUSDT-PERP", status: str = "submitted"
) -> None:
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO order_idempotency
               (client_order_id, ticket_id, order_role, symbol, side, quantity, price, status)
               VALUES (?, ?, 'stop', ?, 'sell', 0.01, 99, ?)""",
            (f"DASTS{ticket_id[:10]}", ticket_id, symbol, status),
        )
        conn.execute(
            """INSERT INTO order_idempotency
               (client_order_id, ticket_id, order_role, symbol, side, quantity, price, status)
               VALUES (?, ?, 'take_profit', ?, 'sell', 0.01, 102, ?)""",
            (f"DATAS{ticket_id[:10]}", ticket_id, symbol, status),
        )
        conn.commit()


def test_reconcile_halts_on_unprotected_open_position(
    ready_db: Path,
    tmp_path: Path,
) -> None:
    """Reproduces the 2026-04-26 incident: an entry filled but stop/TP were
    never accepted by the exchange. Local-↔-exchange counts agree (both
    have a position, neither has open orders) so the legacy mismatch
    detection returns ok. The guard must catch this and halt."""
    _insert_open_live_position(ready_db, ticket_id="ticket-1")
    # Note: ready_db's order_idempotency only contains the entry order;
    # there are no protective rows at all, which is the worst-case orphan.

    client = FakeClient()
    client.open = []  # exchange has no open orders for this symbol
    client.positions = [{"positionAmt": "0.01"}]
    ks = _kill_switch(tmp_path)

    result = LiveReconciler(client=client, db_path=ready_db, kill_switch=ks).run(["BTCUSDT-PERP"])

    assert result.status == "mismatch"
    assert ks.is_active()
    assert any(
        "live_position_missing_protective_orders" in m and "ticket-1" in m
        for m in result.mismatches
    ), result.mismatches


def test_reconcile_clean_when_protective_orders_active(
    ready_db: Path,
    tmp_path: Path,
) -> None:
    """Symmetric to the halt test: an open live position WITH active stop +
    take_profit rows must not trigger the guard."""
    _insert_open_live_position(ready_db, ticket_id="ticket-1")
    _insert_protective_orders(ready_db, ticket_id="ticket-1", status="submitted")

    client = FakeClient()
    client.open = [
        {"clientOrderId": "DAENBTICKET1"},
        {"clientOrderId": "DASTSticket-1"},
    ]
    client.open_algo = [{"clientAlgoId": "DATASticket-1"}]
    client.positions = [{"positionAmt": "0.01"}]
    ks = _kill_switch(tmp_path)

    result = LiveReconciler(client=client, db_path=ready_db, kill_switch=ks).run(["BTCUSDT-PERP"])

    assert result.status == "ok", result.mismatches
    assert not ks.is_active()
