"""Tests for backtest vs shadow comparison."""

from datetime import UTC, datetime

from backtest.runner import BacktestTrade
from reporting.backtest_compare import compare, render_markdown
from storage.db import get_db


def test_compare_matches_shadow_position(db_path) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO setup_events
                (event_id, timestamp, symbol, setup_type, payload, received_at)
            VALUES ('cmp-1', '2026-04-18T00:00:00+00:00', 'BTCUSDT-PERP', 'active', '{}', datetime('now'))
            """
        )
        conn.execute(
            """
            INSERT INTO execution_tickets
                (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
            VALUES ('cmp-t1', 'cmp-1', 'closed', 1, '{}', '2026-04-18T00:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO positions
                (position_id, ticket_id, symbol, direction, status, entry_price, exit_price,
                 quantity, filled_quantity, stop_price, closed_at, net_pnl_usd)
            VALUES ('cmp-p1', 'cmp-t1', 'BTCUSDT-PERP', 'long', 'closed', 100.0, 102.0,
                    1.0, 1.0, 99.0, '2026-04-18T00:10:00+00:00', 2.0)
            """
        )
        conn.commit()

    trade = BacktestTrade(
        event_id="cmp-1",
        symbol="BTCUSDT-PERP",
        strategy="vol_breakout_card",
        direction="long",
        status="closed",
        entry_time=datetime(2026, 4, 18, 0, 1, tzinfo=UTC),
        exit_time=datetime(2026, 4, 18, 0, 2, tzinfo=UTC),
        entry_price=100.0,
        exit_price=101.0,
        exit_reason="horizon_close",
        r_multiple=1.0,
        return_pct=0.01,
    )

    rows = compare([trade], db_path=db_path)

    assert len(rows) == 1
    assert rows[0].shadow_status == "closed"
    assert rows[0].shadow_r == 2.0
    assert rows[0].delta_r == 1.0

    rendered = render_markdown([trade], db_path=db_path)
    assert "Backtest vs Shadow" in rendered
    assert "cmp-1" in rendered
