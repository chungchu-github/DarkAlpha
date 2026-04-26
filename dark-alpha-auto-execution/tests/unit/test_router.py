"""Unit tests for execution.router."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from execution.binance_testnet_broker import LiveBrokerError, LiveOrderAck
from execution.live_safety import LivePreflightError, client_order_id
from execution.router import ModeRouter
from storage.db import get_db, init_db  # noqa: F401
from strategy import config
from strategy.schemas import ExecutionTicket, PlannedOrder


class FakeLiveBroker:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.submitted: list[ExecutionTicket] = []

    def submit_ticket(self, ticket: ExecutionTicket) -> list[LiveOrderAck]:
        self.submitted.append(ticket)
        if self.fail:
            raise LiveBrokerError("fake_rejected")
        entry = next(order for order in ticket.orders if order.role == "entry")
        return [
            LiveOrderAck(
                client_order_id=client_order_id(ticket, entry),
                exchange_order_id="123",
                role="entry",
                symbol="BTCUSDT",
                side="BUY",
                type="LIMIT",
                status="NEW",
                price=100.0,
                quantity=1.0,
            )
        ]


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "r.db"
    monkeypatch.setenv("DB_PATH", str(db))
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "main.yaml").write_text("mode: shadow\n")
    monkeypatch.setattr(config, "_CONFIG_DIR", cfg)
    config.clear_cache()
    init_db(db)
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO setup_events
               (event_id, timestamp, symbol, setup_type, payload, received_at)
               VALUES ('re-1','2026-04-18T00:00:00+00:00','BTCUSDT-PERP','active','{}',
                       datetime('now'))"""
        )
        conn.commit()
    return db


def _ticket(shadow: bool = True) -> ExecutionTicket:
    return ExecutionTicket(
        ticket_id="rt-1",
        source_event_id="re-1",
        symbol="BTCUSDT-PERP",
        direction="long",
        regime="x",
        ranking_score=8.0,
        shadow_mode=shadow,
        gate="gate1",
        entry_price=100.0,
        stop_price=99.0,
        take_profit_price=102.0,
        quantity=1.0,
        notional_usd=100.0,
        leverage=1.0,
        risk_usd=1.0,
        orders=[
            PlannedOrder(
                role="entry",
                side="buy",
                type="limit",
                symbol="BTCUSDT-PERP",
                price=100.0,
                quantity=1.0,
            ),
            PlannedOrder(
                role="stop",
                side="sell",
                type="stop_market",
                symbol="BTCUSDT-PERP",
                price=99.0,
                quantity=1.0,
                reduce_only=True,
            ),
            PlannedOrder(
                role="take_profit",
                side="sell",
                type="limit",
                symbol="BTCUSDT-PERP",
                price=102.0,
                quantity=1.0,
                reduce_only=True,
            ),
        ],
        created_at=datetime.now(tz=UTC).isoformat(),
    )


def test_shadow_dispatch_creates_pending_position(ready_db: Path) -> None:
    router = ModeRouter(db_path=ready_db)
    pos_id = router.dispatch(_ticket(shadow=True))
    assert pos_id

    with get_db(ready_db) as conn:
        row = conn.execute("SELECT status FROM positions WHERE position_id=?", (pos_id,)).fetchone()
    assert row["status"] == "pending"


def test_live_dispatch_blocks_when_global_mode_is_not_live(ready_db: Path) -> None:
    router = ModeRouter(db_path=ready_db)
    with pytest.raises(LivePreflightError, match="live_ticket_while_global_mode_is_not_live"):
        router.dispatch(_ticket(shadow=False))


def test_live_dispatch_preflight_blocks_missing_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: object, **_kw: object) -> None:
        raise LivePreflightError("gate_authorization_missing")

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", _raise)

    router = ModeRouter()
    with pytest.raises(LivePreflightError, match="gate_authorization_missing"):
        router.dispatch(_ticket(shadow=False))


def test_live_dispatch_submits_testnet_order_and_records_ack(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("execution.router.assert_live_pre_order_health", lambda *_a, **_kw: None)

    live_broker = FakeLiveBroker()
    router = ModeRouter(live_broker=live_broker, db_path=ready_db)

    result = router.dispatch(_ticket(shadow=False))

    assert result == "live:rt-1"
    assert live_broker.submitted
    entry_cid = client_order_id(_ticket(shadow=False), _ticket(shadow=False).orders[0])
    with get_db(ready_db) as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_id=?", (entry_cid,)).fetchone()
        idempotency = conn.execute(
            "SELECT status FROM order_idempotency WHERE client_order_id=?", (entry_cid,)
        ).fetchone()
    assert order["exchange_order_id"] == "123"
    assert idempotency["status"] == "submitted"


def test_live_dispatch_marks_ticket_rejected_when_broker_fails(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("execution.router.assert_live_pre_order_health", lambda *_a, **_kw: None)

    router = ModeRouter(live_broker=FakeLiveBroker(fail=True), db_path=ready_db)
    with pytest.raises(LiveBrokerError, match="fake_rejected"):
        router.dispatch(_ticket(shadow=False))

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status, reject_reason FROM execution_tickets WHERE ticket_id='rt-1'"
        ).fetchone()
    assert row["status"] == "rejected"
    assert row["reject_reason"] == "fake_rejected"


def test_live_dispatch_blocks_duplicate_submitted_ticket(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("execution.router.assert_live_pre_order_health", lambda *_a, **_kw: None)

    live_broker = FakeLiveBroker()
    router = ModeRouter(live_broker=live_broker, db_path=ready_db)
    router.dispatch(_ticket(shadow=False))

    with pytest.raises(LivePreflightError, match="duplicate_live_ticket_already_submitted"):
        router.dispatch(_ticket(shadow=False))

    assert len(live_broker.submitted) == 1


# ---------------------------------------------------------------------------
# Task 3 — Pre-order live health gate at the router layer
# ---------------------------------------------------------------------------


def test_live_dispatch_blocks_when_health_gate_fails_kill_switch(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kill switch active → router refuses to call broker.submit_ticket."""
    from execution.live_safety import LivePreOrderHealthError

    def _raise(*_a: object, **_kw: object) -> None:
        raise LivePreOrderHealthError("kill_switch_active")

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", _raise)

    live_broker = FakeLiveBroker()
    router = ModeRouter(live_broker=live_broker, db_path=ready_db)

    with pytest.raises(LivePreOrderHealthError, match="kill_switch_active"):
        router.dispatch(_ticket(shadow=False))

    # Critical: broker must NOT have been called.
    assert live_broker.submitted == []


def test_live_dispatch_blocks_when_public_price_unavailable(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health gate must reject when public price source returns None."""
    from execution.live_safety import LivePreOrderHealthError

    def _raise(*_a: object, **_kw: object) -> None:
        raise LivePreOrderHealthError("public_price_unavailable")

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", _raise)

    live_broker = FakeLiveBroker()
    router = ModeRouter(live_broker=live_broker, db_path=ready_db)

    with pytest.raises(LivePreOrderHealthError, match="public_price_unavailable"):
        router.dispatch(_ticket(shadow=False))
    assert live_broker.submitted == []


def test_live_dispatch_blocks_when_position_query_fails(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health gate must reject when account/position query fails."""
    from execution.live_safety import LivePreOrderHealthError

    def _raise(*_a: object, **_kw: object) -> None:
        raise LivePreOrderHealthError("position_query_failed")

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", _raise)

    live_broker = FakeLiveBroker()
    router = ModeRouter(live_broker=live_broker, db_path=ready_db)

    with pytest.raises(LivePreOrderHealthError, match="position_query_failed"):
        router.dispatch(_ticket(shadow=False))
    assert live_broker.submitted == []


def test_live_dispatch_blocks_when_open_orders_query_fails(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health gate must reject when open-orders query fails."""
    from execution.live_safety import LivePreOrderHealthError

    def _raise(*_a: object, **_kw: object) -> None:
        raise LivePreOrderHealthError("open_orders_query_failed")

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", _raise)

    live_broker = FakeLiveBroker()
    router = ModeRouter(live_broker=live_broker, db_path=ready_db)

    with pytest.raises(LivePreOrderHealthError, match="open_orders_query_failed"):
        router.dispatch(_ticket(shadow=False))
    assert live_broker.submitted == []


def test_live_dispatch_blocks_when_existing_position(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health gate must reject when there's already an exchange position."""
    from execution.live_safety import LivePreOrderHealthError

    def _raise(*_a: object, **_kw: object) -> None:
        raise LivePreOrderHealthError("existing_exchange_position")

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", _raise)

    live_broker = FakeLiveBroker()
    router = ModeRouter(live_broker=live_broker, db_path=ready_db)

    with pytest.raises(LivePreOrderHealthError, match="existing_exchange_position"):
        router.dispatch(_ticket(shadow=False))
    assert live_broker.submitted == []


# ---------------------------------------------------------------------------
# Task 8 — Retry / Idempotency safety
# ---------------------------------------------------------------------------


class TimeoutOnceBroker:
    """First submit raises a timeout-equivalent; the test verifies no second
    submit happens on a retry of the same ticket."""

    def __init__(self) -> None:
        self.calls = 0
        self.client = None

    def submit_ticket(self, ticket: ExecutionTicket) -> list[LiveOrderAck]:
        self.calls += 1
        raise LiveBrokerError("binance_request_failed type=ReadTimeout path=/fapi/v1/order")


def test_live_dispatch_blocks_retry_after_timeout_keeps_reserved(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First submit times out → idempotency rows stay 'reserved'; second
    dispatch of the same ticket must NOT call the broker again."""
    from execution.live_safety import LivePreflightError

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", lambda *_a, **_kw: None)

    broker = TimeoutOnceBroker()
    router = ModeRouter(live_broker=broker, db_path=ready_db)

    with pytest.raises(LiveBrokerError, match="ReadTimeout"):
        router.dispatch(_ticket(shadow=False))

    # After timeout: rows exist with status='reserved' (broker never came back
    # with an ack so no record_live_order_ack was called).
    with get_db(ready_db) as conn:
        statuses = conn.execute(
            "SELECT DISTINCT status FROM order_idempotency WHERE ticket_id='rt-1'"
        ).fetchall()
    assert {row["status"] for row in statuses} == {"reserved"}

    # Second dispatch of SAME ticket must be refused before the broker is hit.
    with pytest.raises(LivePreflightError, match="ticket_reserved_reconcile_required"):
        router.dispatch(_ticket(shadow=False))

    # Critical invariant: broker was called exactly once.
    assert broker.calls == 1


def test_live_dispatch_blocks_retry_after_successful_submit(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a successful submit (rows go to 'submitted'), a retry of the same
    ticket must also be refused — preserves the existing duplicate guard."""
    from execution.live_safety import LivePreflightError

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", lambda *_a, **_kw: None)

    broker = FakeLiveBroker()
    router = ModeRouter(live_broker=broker, db_path=ready_db)

    router.dispatch(_ticket(shadow=False))
    assert broker.submitted, "first dispatch must have invoked broker"

    with pytest.raises(LivePreflightError, match="duplicate_live_ticket_already_submitted"):
        router.dispatch(_ticket(shadow=False))

    # Broker still called exactly once.
    assert len(broker.submitted) == 1


def test_live_dispatch_blocks_retry_after_terminated_ticket(
    ready_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If existing rows are all 'cancelled' or 'rejected', dispatch must
    refuse (audit: no replay of terminated tickets)."""
    from execution.live_safety import LivePreflightError

    monkeypatch.setattr("execution.router.assert_live_pre_order_health", lambda *_a, **_kw: None)

    # Pre-seed: ticket row must exist (FK target) before idempotency rows.
    ticket = _ticket(shadow=False)
    real_cid = client_order_id(ticket, ticket.orders[0])
    with get_db(ready_db) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO execution_tickets
                (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
                VALUES (?, 're-1', 'rejected', 0, '{}', '2026-04-26T00:00:00+00:00')""",
            (ticket.ticket_id,),
        )
        conn.execute(
            """INSERT INTO order_idempotency
                (client_order_id, ticket_id, order_role, symbol, side,
                 quantity, price, status)
                VALUES (?, ?, 'entry', 'BTCUSDT-PERP', 'buy',
                        1.0, 100.0, 'rejected')""",
            (real_cid, ticket.ticket_id),
        )
        conn.commit()

    broker = FakeLiveBroker()
    router = ModeRouter(live_broker=broker, db_path=ready_db)

    with pytest.raises(LivePreflightError, match="ticket_already_terminated"):
        router.dispatch(ticket)

    assert broker.submitted == []
