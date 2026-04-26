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
    cfg.write_text("breakers:\n  - {name: daily_loss, condition: 'x', action: halt_24h}\n")
    breaker = CircuitBreaker(db_path=ready_db, config_path=cfg)
    breaker.trip("daily_loss", reason="test")
    gate = RiskGate(kill_switch=ks, breaker=breaker, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "circuit_breaker_halt"


def test_breaker_no_new_entries_rejects(
    ready_db: Path, ks: KillSwitch, tmp_path: Path, setup_event: SetupEvent
) -> None:
    cfg = tmp_path / "breakers.yaml"
    cfg.write_text("breakers:\n  - {name: vol, condition: 'x', action: no_new_entries}\n")
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


def test_daily_symbol_ticket_cap(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from storage.db import get_db

    monkeypatch.setattr(
        "strategy.risk_gate.risk_gate_config",
        lambda: {
            "max_concurrent_positions": 3,
            "max_tickets_per_day": 10,
            "max_tickets_per_symbol_per_day": 2,
            "max_tickets_per_strategy_per_day": 0,
            "min_equity_to_trade": 1000.0,
            "allow_duplicate_symbol": False,
        },
    )
    today = datetime.now(tz=UTC).isoformat()
    with get_db(ready_db) as conn:
        for i in range(2):
            event_id = f"sym-e{i}"
            conn.execute(
                """INSERT INTO setup_events
                   (event_id, timestamp, symbol, setup_type, payload, received_at)
                   VALUES (?, ?, ?, 'active', ?, datetime('now'))""",
                (
                    event_id,
                    today,
                    setup_event.symbol,
                    setup_event.model_dump_json(),
                ),
            )
            conn.execute(
                """INSERT INTO execution_tickets
                   (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
                   VALUES (?, ?, 'created', 1, '{}', ?)""",
                (f"sym-t{i}", event_id, today),
            )
        conn.commit()

    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "daily_symbol_ticket_cap"


def test_daily_strategy_ticket_cap(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from storage.db import get_db

    monkeypatch.setattr(
        "strategy.risk_gate.risk_gate_config",
        lambda: {
            "max_concurrent_positions": 3,
            "max_tickets_per_day": 10,
            "max_tickets_per_symbol_per_day": 0,
            "max_tickets_per_strategy_per_day": 2,
            "min_equity_to_trade": 1000.0,
            "allow_duplicate_symbol": False,
        },
    )
    today = datetime.now(tz=UTC).isoformat()
    with get_db(ready_db) as conn:
        for i in range(2):
            event = setup_event.model_copy(
                update={"event_id": f"strat-e{i}", "symbol": f"ALT{i}-PERP"}
            )
            conn.execute(
                """INSERT INTO setup_events
                   (event_id, timestamp, symbol, setup_type, payload, received_at)
                   VALUES (?, ?, ?, 'active', ?, datetime('now'))""",
                (
                    event.event_id,
                    today,
                    event.symbol,
                    event.model_dump_json(),
                ),
            )
            conn.execute(
                """INSERT INTO execution_tickets
                   (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
                   VALUES (?, ?, 'created', 1, '{}', ?)""",
                (f"strat-t{i}", event.event_id, today),
            )
        conn.commit()

    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "daily_strategy_ticket_cap"


def test_max_consecutive_losses_rejects(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    from storage.db import get_db

    monkeypatch.setattr(
        "strategy.risk_gate.risk_gate_config",
        lambda: {
            "max_concurrent_positions": 3,
            "max_tickets_per_day": 10,
            "max_tickets_per_symbol_per_day": 0,
            "max_tickets_per_strategy_per_day": 0,
            "max_consecutive_losses": 3,
            "max_weekly_loss_pct": 0,
            "min_equity_to_trade": 1000.0,
            "allow_duplicate_symbol": False,
        },
    )
    now = datetime.now(tz=UTC)
    with get_db(ready_db) as conn:
        for i in range(3):
            conn.execute(
                """
                INSERT INTO positions
                    (position_id, symbol, direction, status, quantity, closed_at, net_pnl_usd)
                VALUES (?, ?, 'long', 'closed', 1.0, ?, -10.0)
                """,
                (f"loss-{i}", f"LOSS{i}-PERP", (now - timedelta(minutes=i)).isoformat()),
            )
        conn.commit()

    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "max_consecutive_losses"


def test_open_positions_db_error_rejects_fail_closed(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even an unrecognised DB error during open-positions lookup must fail closed."""
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)

    def _boom(self: RiskGate) -> tuple[int, set[str]]:
        from strategy.risk_gate import RiskGateDataError

        raise RiskGateDataError("open_positions_unavailable", "boom")

    monkeypatch.setattr(RiskGate, "_open_positions", _boom)

    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None
    assert rej.reason == "open_positions_unavailable"
    assert rej.stage == "risk_gate"


def test_tickets_today_db_error_rejects_fail_closed(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)

    def _boom(self: RiskGate) -> int:
        from strategy.risk_gate import RiskGateDataError

        raise RiskGateDataError("ticket_count_unavailable", "boom")

    monkeypatch.setattr(RiskGate, "_tickets_today", _boom)

    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "ticket_count_unavailable"


def test_consecutive_losses_db_error_rejects_fail_closed(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "strategy.risk_gate.risk_gate_config",
        lambda: {
            "max_concurrent_positions": 3,
            "max_tickets_per_day": 10,
            "max_consecutive_losses": 3,
            "max_weekly_loss_pct": 0,
            "min_equity_to_trade": 1000.0,
            "allow_duplicate_symbol": False,
        },
    )
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)

    def _boom(self: RiskGate) -> int:
        from strategy.risk_gate import RiskGateDataError

        raise RiskGateDataError("pnl_state_unavailable", "boom")

    monkeypatch.setattr(RiskGate, "_consecutive_losses", _boom)

    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "pnl_state_unavailable"


def test_weekly_loss_db_error_rejects_fail_closed(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "strategy.risk_gate.risk_gate_config",
        lambda: {
            "max_concurrent_positions": 3,
            "max_tickets_per_day": 10,
            "max_consecutive_losses": 0,
            "max_weekly_loss_pct": 0.03,
            "min_equity_to_trade": 1000.0,
            "allow_duplicate_symbol": False,
        },
    )
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)

    def _boom(self: RiskGate) -> float:
        from strategy.risk_gate import RiskGateDataError

        raise RiskGateDataError("pnl_state_unavailable", "boom")

    monkeypatch.setattr(RiskGate, "_weekly_realized_loss_usd", _boom)

    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "pnl_state_unavailable"


def test_unexpected_exception_rejects_fail_closed(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any non-RiskGateDataError exception inside check() also fails closed."""
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)

    def _boom(self: RiskGate) -> tuple[int, set[str]]:
        raise RuntimeError("unexpected sqlite corruption")

    monkeypatch.setattr(RiskGate, "_open_positions", _boom)

    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None
    assert rej.reason == "risk_state_unavailable"
    assert rej.stage == "risk_gate"


def test_real_db_failure_triggers_fail_closed(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a real sqlite error converts to a fail-closed Rejection.

    ``get_db`` re-runs migrations on every call, so we cannot simply DROP a
    table. Instead force the underlying connection to error on execute().
    """
    import contextlib
    import sqlite3
    from collections.abc import Generator

    @contextlib.contextmanager
    def _broken_db(path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(":memory:")

        def _raise(*_a: object, **_kw: object) -> None:
            raise sqlite3.OperationalError("disk I/O error")

        conn.execute = _raise  # type: ignore[method-assign]
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr("strategy.risk_gate.get_db", _broken_db)

    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None
    assert rej.stage == "risk_gate"
    # First failing query depends on default config; under shipped risk_gate.yaml
    # max_consecutive_losses > 0 so _consecutive_losses runs first.
    assert rej.reason in {
        "pnl_state_unavailable",
        "open_positions_unavailable",
        "ticket_count_unavailable",
    }


def test_check_never_raises_to_caller(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check() must never let an exception escape to the receiver."""
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)

    def _boom(self: RiskGate) -> int:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(RiskGate, "_tickets_today", _boom)

    # Should return Rejection, not raise.
    result = gate.check(setup_event, equity_usd=10_000.0)
    assert result is not None
    assert result.stage == "risk_gate"


def test_daily_loss_cap_rejects(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 7 — when today's realized loss reaches max_daily_loss_usd, reject."""
    from datetime import UTC, datetime

    from storage.db import get_db

    monkeypatch.setattr(
        "strategy.risk_gate.risk_gate_config",
        lambda: {
            "max_concurrent_positions": 3,
            "max_tickets_per_day": 10,
            "max_tickets_per_symbol_per_day": 0,
            "max_tickets_per_strategy_per_day": 0,
            "max_consecutive_losses": 0,
            "max_weekly_loss_pct": 0,
            "max_daily_loss_usd": 50.0,
            "min_equity_to_trade": 1000.0,
            "allow_duplicate_symbol": False,
        },
    )
    today_iso = datetime.now(tz=UTC).isoformat()
    with get_db(ready_db) as conn:
        conn.execute(
            """
            INSERT INTO positions
                (position_id, symbol, direction, status, quantity, closed_at, net_pnl_usd)
            VALUES ('today-loss', 'BTCUSDT-PERP', 'long', 'closed', 1.0, ?, -75.0)
            """,
            (today_iso,),
        )
        conn.commit()

    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "daily_loss_cap"


def test_daily_loss_cap_respects_yesterday_losses(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yesterday's losses must not count against today's daily loss cap."""
    from datetime import UTC, datetime, timedelta

    from storage.db import get_db

    monkeypatch.setattr(
        "strategy.risk_gate.risk_gate_config",
        lambda: {
            "max_concurrent_positions": 3,
            "max_tickets_per_day": 10,
            "max_tickets_per_symbol_per_day": 0,
            "max_tickets_per_strategy_per_day": 0,
            "max_consecutive_losses": 0,
            "max_weekly_loss_pct": 0,
            "max_daily_loss_usd": 50.0,
            "min_equity_to_trade": 1000.0,
            "allow_duplicate_symbol": False,
        },
    )
    yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    with get_db(ready_db) as conn:
        conn.execute(
            """
            INSERT INTO positions
                (position_id, symbol, direction, status, quantity, closed_at, net_pnl_usd)
            VALUES ('yest-loss', 'BTCUSDT-PERP', 'long', 'closed', 1.0, ?, -75.0)
            """,
            (yesterday,),
        )
        conn.commit()

    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    # Should NOT reject — yesterday's loss doesn't count today.
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is None


def test_weekly_loss_cap_rejects(
    ready_db: Path,
    ks: KillSwitch,
    cb: CircuitBreaker,
    setup_event: SetupEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from storage.db import get_db

    monkeypatch.setattr(
        "strategy.risk_gate.risk_gate_config",
        lambda: {
            "max_concurrent_positions": 3,
            "max_tickets_per_day": 10,
            "max_tickets_per_symbol_per_day": 0,
            "max_tickets_per_strategy_per_day": 0,
            "max_consecutive_losses": 0,
            "max_weekly_loss_pct": 0.03,
            "min_equity_to_trade": 1000.0,
            "allow_duplicate_symbol": False,
        },
    )
    with get_db(ready_db) as conn:
        conn.execute(
            """
            INSERT INTO positions
                (position_id, symbol, direction, status, quantity, closed_at, net_pnl_usd)
            VALUES ('weekly-loss', 'BTCUSDT-PERP', 'long', 'closed', 1.0, ?, -300.0)
            """,
            (datetime.now(tz=UTC).isoformat(),),
        )
        conn.commit()

    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    rej = gate.check(setup_event, equity_usd=10_000.0)
    assert rej is not None and rej.reason == "weekly_loss_cap"
