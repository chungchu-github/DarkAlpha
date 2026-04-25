"""Unit tests for CircuitBreaker — ≥90% coverage required (spec Phase 1)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from safety.circuit_breaker import BreakerState, CircuitBreaker


@pytest.fixture()
def cb(tmp_path: Path) -> CircuitBreaker:
    cfg = Path(__file__).parent.parent.parent / "config" / "breakers.yaml"
    return CircuitBreaker(db_path=tmp_path / "test.db", config_path=cfg)


# ------------------------------------------------------------------
# Initial state
# ------------------------------------------------------------------


def test_initially_not_tripped(cb: CircuitBreaker) -> None:
    assert not cb.is_tripped()


def test_get_active_action_none_initially(cb: CircuitBreaker) -> None:
    assert cb.get_active_action() is None


def test_rule_names_loaded(cb: CircuitBreaker) -> None:
    names = cb.rule_names()
    assert "daily_loss" in names
    assert "consecutive_losses" in names
    assert "api_error_spike" in names
    assert "volatility_spike" in names
    assert "drawdown" in names


# ------------------------------------------------------------------
# trip()
# ------------------------------------------------------------------


def test_trip_daily_loss(cb: CircuitBreaker) -> None:
    cb.trip("daily_loss", reason="pnl=-4%")
    assert cb.is_tripped()


def test_trip_consecutive_losses(cb: CircuitBreaker) -> None:
    cb.trip("consecutive_losses", reason="3 in a row")
    assert cb.is_tripped()


def test_trip_api_error_spike(cb: CircuitBreaker) -> None:
    cb.trip("api_error_spike", reason="5 errors in 5min")
    assert cb.is_tripped()


def test_trip_sets_tripped_at(cb: CircuitBreaker) -> None:
    cb.trip("daily_loss", reason="test")
    state = cb.get_state("daily_loss")
    assert state is not None
    assert state.tripped_at is not None


def test_trip_halt_24h_sets_clear_at(cb: CircuitBreaker) -> None:
    cb.trip("daily_loss", reason="test")
    state = cb.get_state("daily_loss")
    assert state is not None
    assert state.clear_at is not None


def test_trip_halt_until_manual_has_no_clear_at(cb: CircuitBreaker) -> None:
    cb.trip("api_error_spike", reason="test")
    state = cb.get_state("api_error_spike")
    assert state is not None
    assert state.clear_at is None


def test_get_active_action_returns_action(cb: CircuitBreaker) -> None:
    cb.trip("daily_loss", reason="test")
    action = cb.get_active_action()
    assert action == "halt_24h"


# ------------------------------------------------------------------
# reset()
# ------------------------------------------------------------------


def test_reset_clears_tripped_state(cb: CircuitBreaker) -> None:
    cb.trip("daily_loss", reason="test")
    cb.reset("daily_loss")
    assert not cb.is_tripped()


def test_reset_unknown_breaker_is_safe(cb: CircuitBreaker) -> None:
    cb.reset("nonexistent_breaker")  # must not raise


def test_reset_all(cb: CircuitBreaker) -> None:
    cb.trip("daily_loss", reason="a")
    cb.trip("consecutive_losses", reason="b")
    cb.reset_all()
    assert not cb.is_tripped()


# ------------------------------------------------------------------
# Non-halt actions don't count as "tripped" for is_tripped()
# ------------------------------------------------------------------


def test_volatility_spike_no_new_entries_not_a_halt(cb: CircuitBreaker) -> None:
    cb.trip("volatility_spike", reason="atr spike")
    # volatility_spike action = no_new_entries — not a halt action
    assert not cb.is_tripped()


def test_drawdown_halve_position_size_not_a_halt(cb: CircuitBreaker) -> None:
    cb.trip("drawdown", reason="10% dd")
    assert not cb.is_tripped()


def test_get_active_action_returns_non_halt_action(cb: CircuitBreaker) -> None:
    cb.trip("volatility_spike", reason="test")
    assert cb.get_active_action() == "no_new_entries"


# ------------------------------------------------------------------
# Auto-clear (time-based expiry)
# ------------------------------------------------------------------


def test_auto_clear_expired_breaker(tmp_path: Path) -> None:
    cfg = Path(__file__).parent.parent.parent / "config" / "breakers.yaml"
    cb = CircuitBreaker(db_path=tmp_path / "ac.db", config_path=cfg)

    cb.trip("daily_loss", reason="test")

    # Manually set clear_at to the past to simulate expiry
    past = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
    from storage.db import get_db

    with get_db(tmp_path / "ac.db") as conn:
        conn.execute(
            "UPDATE circuit_breaker_state SET clear_at=? WHERE name='daily_loss'",
            (past,),
        )
        conn.commit()

    # Next call to is_tripped() should auto-clear it
    assert not cb.is_tripped()


# ------------------------------------------------------------------
# Persistence across instances
# ------------------------------------------------------------------


def test_trip_persists_across_instances(tmp_path: Path) -> None:
    cfg = Path(__file__).parent.parent.parent / "config" / "breakers.yaml"
    db = tmp_path / "persist.db"

    cb1 = CircuitBreaker(db_path=db, config_path=cfg)
    cb1.trip("api_error_spike", reason="spike")

    cb2 = CircuitBreaker(db_path=db, config_path=cfg)
    assert cb2.is_tripped()


# ------------------------------------------------------------------
# all_states()
# ------------------------------------------------------------------


def test_all_states_empty_initially(cb: CircuitBreaker) -> None:
    assert cb.all_states() == {}


def test_all_states_after_trip(cb: CircuitBreaker) -> None:
    cb.trip("daily_loss", reason="test")
    states = cb.all_states()
    assert "daily_loss" in states
    assert isinstance(states["daily_loss"], BreakerState)


# ------------------------------------------------------------------
# Missing config file
# ------------------------------------------------------------------


def test_missing_config_file_loads_empty_rules(tmp_path: Path) -> None:
    cb = CircuitBreaker(
        db_path=tmp_path / "t.db",
        config_path=tmp_path / "nonexistent.yaml",
    )
    assert cb.rule_names() == []
    assert not cb.is_tripped()


# ------------------------------------------------------------------
# Alert failure does not propagate
# ------------------------------------------------------------------


def test_trip_succeeds_even_if_alert_fails(
    cb: CircuitBreaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad_alert(level: str, msg: str) -> None:
        raise RuntimeError("telegram down")

    monkeypatch.setattr(
        "safety.circuit_breaker.CircuitBreaker._send_alert",
        bad_alert,
    )
    cb.trip("daily_loss", reason="test")  # must not raise
    assert cb.is_tripped()
