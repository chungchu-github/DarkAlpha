"""Unit tests for daily + weekly reporting."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from reporting.daily import write_snapshot
from reporting.performance import render_markdown, summarize
from reporting.weekly import generate
from storage.db import get_db, init_db


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "rep.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    return db


def _insert_closed_position(
    db: Path,
    closed_on: date,
    gross: float,
    fees: float,
    net: float,
) -> None:
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO positions
               (position_id, ticket_id, symbol, direction, status,
                quantity, filled_quantity, closed_at, gross_pnl_usd,
                fees_usd, net_pnl_usd, shadow_mode)
               VALUES (?, NULL, 'BTCUSDT-PERP', 'long', 'closed', 1.0, 1.0,
                       ?, ?, ?, ?, 1)""",
            (
                f"p-{closed_on.isoformat()}-{net}",
                datetime(closed_on.year, closed_on.month, closed_on.day, 12, 0,
                         tzinfo=UTC).isoformat(),
                gross, fees, net,
            ),
        )
        conn.commit()


def _insert_closed_position_with_ticket(
    db: Path,
    *,
    position_id: str,
    ticket_id: str,
    event_id: str,
    symbol: str,
    regime: str,
    net: float,
) -> None:
    payload = (
        f'{{"ticket_id":"{ticket_id}","source_event_id":"{event_id}","symbol":"{symbol}",'
        f'"direction":"long","regime":"{regime}","ranking_score":8.0,'
        '"shadow_mode":true,"gate":"gate1","entry_price":100.0,'
        '"stop_price":99.0,"quantity":1.0,"notional_usd":100.0,'
        '"leverage":1.0,"risk_usd":1.0,"orders":[],"created_at":"2026-04-18T00:00:00+00:00",'
        f'"metadata":{{"event_metadata":{{"strategy":"{regime}"}}}}}}'
    )
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO setup_events
               (event_id, timestamp, symbol, setup_type, payload, received_at)
               VALUES (?, '2026-04-18T00:00:00+00:00', ?, 'active', '{}', datetime('now'))""",
            (event_id, symbol),
        )
        conn.execute(
            """INSERT INTO execution_tickets
               (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
               VALUES (?, ?, 'closed', 1, ?, '2026-04-18T00:00:00+00:00')""",
            (ticket_id, event_id, payload),
        )
        conn.execute(
            """INSERT INTO positions
               (position_id, ticket_id, symbol, direction, status, quantity, filled_quantity,
                closed_at, gross_pnl_usd, fees_usd, net_pnl_usd, shadow_mode)
               VALUES (?, ?, ?, 'long', 'closed', 1.0, 1.0,
                       '2026-04-18T01:00:00+00:00', ?, 0.5, ?, 1)""",
            (position_id, ticket_id, symbol, net + 0.5, net),
        )
        conn.commit()


def test_daily_snapshot_with_no_trades(ready_db: Path) -> None:
    d = date(2026, 4, 10)
    stats = write_snapshot(d, db_path=ready_db)
    assert stats.trade_count == 0
    assert stats.starting_equity == stats.ending_equity


def test_daily_snapshot_aggregates_pnl(ready_db: Path) -> None:
    d = date(2026, 4, 11)
    _insert_closed_position(ready_db, d, gross=10.0, fees=0.5, net=9.5)
    _insert_closed_position(ready_db, d, gross=-5.0, fees=0.5, net=-5.5)

    stats = write_snapshot(d, db_path=ready_db)
    assert stats.trade_count == 2
    assert stats.win_count == 1
    assert stats.loss_count == 1
    assert stats.gross_pnl == pytest.approx(5.0)
    assert stats.fees == pytest.approx(1.0)
    assert stats.net_pnl == pytest.approx(4.0)


def test_daily_snapshot_chains_equity(ready_db: Path) -> None:
    d1 = date(2026, 4, 12)
    d2 = d1 + timedelta(days=1)
    _insert_closed_position(ready_db, d1, gross=100.0, fees=1.0, net=99.0)
    s1 = write_snapshot(d1, db_path=ready_db)
    _insert_closed_position(ready_db, d2, gross=50.0, fees=0.5, net=49.5)
    s2 = write_snapshot(d2, db_path=ready_db)

    assert s2.starting_equity == pytest.approx(s1.ending_equity)
    assert s2.ending_equity == pytest.approx(s1.ending_equity + 49.5)


def test_weekly_report_empty_window(tmp_path: Path, ready_db: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    import reporting.weekly as w
    monkeypatch.setattr(w, "_REPORTS_DIR", tmp_path / "reports")

    path = generate(date(2026, 4, 19), db_path=ready_db)
    assert path.exists()
    content = path.read_text()
    assert "Shadow Mode Weekly Report" in content
    assert "No daily snapshots" in content


def test_weekly_report_with_data(tmp_path: Path, ready_db: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    import reporting.weekly as w
    monkeypatch.setattr(w, "_REPORTS_DIR", tmp_path / "reports")

    for i in range(3):
        d = date(2026, 4, 13) + timedelta(days=i)
        _insert_closed_position(ready_db, d, gross=10.0 * (i + 1),
                                fees=0.5, net=10.0 * (i + 1) - 0.5)
        write_snapshot(d, db_path=ready_db)

    path = generate(date(2026, 4, 19), db_path=ready_db)
    content = path.read_text()
    assert "## Summary" in content
    assert "Daily breakdown" in content
    assert "2026-04-13" in content


def test_performance_report_groups_by_symbol_strategy_regime(ready_db: Path) -> None:
    _insert_closed_position_with_ticket(
        ready_db,
        position_id="p1",
        ticket_id="t1",
        event_id="e1",
        symbol="BTCUSDT-PERP",
        regime="vol_breakout_card",
        net=10.0,
    )
    _insert_closed_position_with_ticket(
        ready_db,
        position_id="p2",
        ticket_id="t2",
        event_id="e2",
        symbol="ETHUSDT-PERP",
        regime="fake_breakout_reversal",
        net=-5.0,
    )

    grouped = summarize(db_path=ready_db)
    assert {bucket.key for bucket in grouped["symbol"]} == {"BTCUSDT-PERP", "ETHUSDT-PERP"}
    assert {bucket.key for bucket in grouped["strategy"]} == {
        "vol_breakout_card",
        "fake_breakout_reversal",
    }

    report = render_markdown(db_path=ready_db)
    assert "By Symbol" in report
    assert "BTCUSDT-PERP" in report
    assert "vol_breakout_card" in report
