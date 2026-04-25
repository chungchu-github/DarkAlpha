"""Unit tests for execution.router."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from execution.router import ModeRouter
from storage.db import get_db, init_db  # noqa: F401
from strategy.schemas import ExecutionTicket, PlannedOrder


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "r.db"
    monkeypatch.setenv("DB_PATH", str(db))
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
        ticket_id="rt-1", source_event_id="re-1", symbol="BTCUSDT-PERP",
        direction="long", regime="x", ranking_score=8.0, shadow_mode=shadow,
        gate="gate1", entry_price=100.0, stop_price=99.0, take_profit_price=102.0,
        quantity=1.0, notional_usd=100.0, leverage=1.0, risk_usd=1.0,
        orders=[
            PlannedOrder(role="entry", side="buy", type="limit",
                         symbol="BTCUSDT-PERP", price=100.0, quantity=1.0),
            PlannedOrder(role="stop", side="sell", type="stop_market",
                         symbol="BTCUSDT-PERP", price=99.0, quantity=1.0, reduce_only=True),
            PlannedOrder(role="take_profit", side="sell", type="limit",
                         symbol="BTCUSDT-PERP", price=102.0, quantity=1.0, reduce_only=True),
        ],
        created_at=datetime.now(tz=UTC).isoformat(),
    )


def test_shadow_dispatch_opens_position(ready_db: Path) -> None:
    router = ModeRouter(db_path=ready_db)
    pos_id = router.dispatch(_ticket(shadow=True))
    assert pos_id

    with get_db(ready_db) as conn:
        row = conn.execute(
            "SELECT status FROM positions WHERE position_id=?", (pos_id,)
        ).fetchone()
    assert row["status"] == "open"


def test_live_dispatch_raises() -> None:
    router = ModeRouter()
    with pytest.raises(NotImplementedError):
        router.dispatch(_ticket(shadow=False))
