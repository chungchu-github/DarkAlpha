"""Unit tests for strategy.risk_gate — ≥90% coverage target."""

from pathlib import Path

import pytest

from safety.circuit_breaker import CircuitBreaker
from safety.kill_switch import KillSwitch
from signal_adapter.schemas import SetupEvent
from storage.db import init_db
from strategy import config
from strategy.risk_gate import RiskGate


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    config.clear_cache()


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "rg.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    return db


@pytest.fixture()
def ks(tmp_path: Path) -> KillSwitch:
    return KillSwitch(sentinel_path=tmp_path / "kill")


@pytest.fixture()
def cb(ready_db: Path, tmp_path: Path) -> CircuitBreaker:
    return CircuitBreaker(db_path=ready_db, config_path=tmp_path / "no-breakers.yaml")


def test_all_clear_passes(
    ready_db: Path, ks: KillSwitch, cb: CircuitBreaker, setup_event: SetupEvent
) -> None:
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    assert gate.check(setup_event, equity_usd=10_000.0) is None


def test_kill_switch_rejects(
    ready_db: Path, ks: KillSwitch, cb: CircuitBreaker, setup_event: SetupEvent
) -> None:
    ks.activate(reason="test")
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "kill_switch_active"


def test_breaker_halt_rejects(
    ready_db: Path, ks: KillSwitch, tmp_path: Path, setup_event: SetupEvent
) -> None:
    cfg = tmp_path / "breakers.yaml"
    cfg.write_text(
        "breakers:\n  - {name: daily_loss, condition: 'x', action: halt_24h}\n"
    )
    breaker = CircuitBreaker(db_path=ready_db, config_path=cfg)
    breaker.trip("daily_loss", reason="test")
    gate = RiskGate(kill_switch=ks, breaker=breaker, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "circuit_breaker_halt"


def test_breaker_no_new_entries_rejects(
    ready_db: Path, ks: KillSwitch, tmp_path: Path, setup_event: SetupEvent
) -> None:
    cfg = tmp_path / "breakers.yaml"
    cfg.write_text(
        "breakers:\n  - {name: vol, condition: 'x', action: no_new_entries}\n"
    )
    breaker = CircuitBreaker(db_path=ready_db, config_path=cfg)
    breaker.trip("vol", reason="test")
    gate = RiskGate(kill_switch=ks, breaker=breaker, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "no_new_entries"


def test_below_min_equity(
    ready_db: Path, ks: KillSwitch, cb: CircuitBreaker, setup_event: SetupEvent
) -> None:
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=1.0)
    assert rej is not None and rej.reason == "below_min_equity"


def test_max_positions_reached(
    ready_db: Path, ks: KillSwitch, cb: CircuitBreaker, setup_event: SetupEvent
) -> None:
    from storage.db import get_db

    with get_db(ready_db) as conn:
        for i, sym in enumerate(["AAA-PERP", "BBB-PERP", "CCC-PERP"]):
            conn.execute(
                """INSERT INTO positions (position_id, symbol, direction, status, quantity)
                   VALUES (?, ?, 'long', 'open', 1.0)""",
                (f"p{i}", sym),
            )
        conn.commit()
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "max_positions_reached"


def test_duplicate_symbol(
    ready_db: Path, ks: KillSwitch, cb: CircuitBreaker, setup_event: SetupEvent
) -> None:
    from storage.db import get_db

    with get_db(ready_db) as conn:
        conn.execute(
            """INSERT INTO positions (position_id, symbol, direction, status, quantity)
               VALUES ('p1', ?, 'long', 'open', 1.0)""",
            (setup_event.symbol,),
        )
        conn.commit()
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "duplicate_symbol"


def test_daily_ticket_cap(
    ready_db: Path, ks: KillSwitch, cb: CircuitBreaker, setup_event: SetupEvent
) -> None:
    from datetime import UTC, datetime

    from storage.db import get_db

    today = datetime.now(tz=UTC).isoformat()
    with get_db(ready_db) as conn:
        for i in range(5):
            conn.execute(
                """INSERT INTO setup_events
                   (event_id, timestamp, symbol, setup_type, payload, received_at)
                   VALUES (?, ?, 'BTC-PERP', 'active', '{}', datetime('now'))""",
                (f"e{i}", today),
            )
            conn.execute(
                """INSERT INTO execution_tickets
                   (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
                   VALUES (?, ?, 'created', 1, '{}', ?)""",
                (f"t{i}", f"e{i}", today),
            )
        conn.commit()
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "daily_ticket_cap"
