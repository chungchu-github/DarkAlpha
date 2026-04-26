"""Unit tests for telegram.handlers — dispatch + output content."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from safety.kill_switch import KillSwitch
from storage.db import get_db, init_db
from telegram import handlers


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "tg.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    return db


@pytest.fixture()
def captured() -> list[str]:
    return []


@pytest.fixture()
def reply(captured: list[str]) -> handlers.Reply:
    def _r(msg: str) -> None:
        captured.append(msg)

    return _r


def _seed_open_position(db: Path, symbol: str = "BTCUSDT-PERP") -> None:
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO positions
               (position_id, symbol, direction, status, quantity,
                entry_price, stop_price, take_profit_price, opened_at)
               VALUES ('p1', ?, 'long', 'open', 0.1, 100.0, 99.0, 102.0,
                       datetime('now'))""",
            (symbol,),
        )
        conn.commit()


def _seed_closed_position_today(db: Path, net: float = 5.0) -> None:
    today = datetime.now(tz=UTC).isoformat()
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO positions
               (position_id, symbol, direction, status, quantity, closed_at,
                gross_pnl_usd, fees_usd, net_pnl_usd)
               VALUES ('c1', 'BTCUSDT-PERP', 'long', 'closed', 0.1, ?,
                       ?, 0.5, ?)""",
            (today, net + 0.5, net),
        )
        conn.commit()


def test_help_returns_command_list(reply: handlers.Reply, captured: list[str]) -> None:
    handlers.handle_help(reply, [])
    assert "/status" in captured[0]
    assert "/halt" in captured[0]


def test_positions_empty(ready_db: Path, reply: handlers.Reply, captured: list[str]) -> None:
    handlers.handle_positions(reply, [])
    assert captured == ["No open positions."]


def test_positions_lists_open(ready_db: Path, reply: handlers.Reply, captured: list[str]) -> None:
    _seed_open_position(ready_db)
    handlers.handle_positions(reply, [])
    assert "BTCUSDT-PERP" in captured[0]
    assert "qty=0.1000" in captured[0]


def test_pnl_today_counts_net(ready_db: Path, reply: handlers.Reply, captured: list[str]) -> None:
    _seed_closed_position_today(ready_db, net=7.5)
    handlers.handle_pnl_today(reply, [])
    assert "trades: 1" in captured[0]
    assert "+7.50" in captured[0]


def test_halt_activates_kill_switch(
    ready_db: Path,
    tmp_path: Path,
    reply: handlers.Reply,
    captured: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = tmp_path / "kill"
    ks = KillSwitch(sentinel_path=sentinel)
    monkeypatch.setattr("telegram.handlers.get_kill_switch", lambda: ks)
    handlers.handle_halt(reply, ["because", "it's", "bad"])
    assert ks.is_active()
    assert "ACTIVATED" in captured[0]
    assert "because it's bad" in captured[0]


def test_resume_noop_if_clear(
    tmp_path: Path,
    reply: handlers.Reply,
    captured: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ks = KillSwitch(sentinel_path=tmp_path / "kill")
    monkeypatch.setattr("telegram.handlers.get_kill_switch", lambda: ks)
    handlers.handle_resume(reply, [])
    assert "already clear" in captured[0]


def test_resume_clears_kill_switch(
    tmp_path: Path,
    reply: handlers.Reply,
    captured: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ks = KillSwitch(sentinel_path=tmp_path / "kill")
    ks.activate(reason="test")
    monkeypatch.setattr("telegram.handlers.get_kill_switch", lambda: ks)
    handlers.handle_resume(reply, [])
    assert not ks.is_active()
    assert "cleared" in captured[0]


def test_status_shows_all_sections(
    ready_db: Path,
    tmp_path: Path,
    reply: handlers.Reply,
    captured: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_open_position(ready_db)
    _seed_closed_position_today(ready_db, net=3.0)
    ks = KillSwitch(sentinel_path=tmp_path / "kill")
    monkeypatch.setattr("telegram.handlers.get_kill_switch", lambda: ks)
    handlers.handle_status(reply, [])
    out = captured[0]
    assert "Kill switch" in out
    assert "Open positions: 1" in out
    assert "1 trade(s)" in out


def test_breakers_none_initially(
    ready_db: Path, reply: handlers.Reply, captured: list[str]
) -> None:
    handlers.handle_breakers(reply, [])
    assert captured  # wrote something (empty or list)


def test_dispatch_table_coverage() -> None:
    for cmd in (
        "/help",
        "/status",
        "/halt",
        "/resume",
        "/positions",
        "/pnl_today",
        "/breakers",
        "/start",
    ):
        assert cmd in handlers.DISPATCH
