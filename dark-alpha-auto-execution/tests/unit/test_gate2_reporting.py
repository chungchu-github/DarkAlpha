"""Tests for Gate 2 report generation."""

from pathlib import Path

from reporting.gate2 import write_report
from storage.db import get_db, init_db


def test_gate2_report_writes_ticket_orders_positions_and_reconcile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = tmp_path / "gate2.db"
    reports = tmp_path / "reports"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    with get_db(db) as conn:
        conn.execute(
            """INSERT INTO setup_events
               (event_id, timestamp, symbol, setup_type, payload, received_at)
               VALUES ('evt-g2','2026-04-26T00:00:00+00:00','ETHUSDT-PERP','active','{}',
                       datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO execution_tickets
               (ticket_id, source_event_id, status, shadow_mode, payload, created_at)
               VALUES ('ticket-g2','evt-g2','closed',0,'{}','2026-04-26T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO orders
               (order_id, ticket_id, exchange_order_id, side, type, symbol, price,
                quantity, status, submitted_at, fill_price, fill_quantity)
               VALUES ('DAENB1','ticket-g2','ex-1','buy','LIMIT','ETHUSDT',2300,
                       0.05,'filled',datetime('now'),2300,0.05)"""
        )
        conn.execute(
            """INSERT INTO positions
               (position_id, ticket_id, symbol, direction, status, entry_price,
                exit_price, quantity, filled_quantity, exit_reason, net_pnl_usd,
                shadow_mode)
               VALUES ('pos-g2','ticket-g2','ETHUSDT-PERP','long','closed',2300,
                       2400,0.05,0,'take_profit',5,0)"""
        )
        conn.execute(
            """INSERT INTO reconciliation_runs (run_id, status, details)
               VALUES ('rec-g2','ok','{"status":"ok"}')"""
        )
        conn.commit()

    path = write_report(ticket_id="ticket-g2", db_path=db, reports_dir=reports)
    text = path.read_text()

    assert "Gate 2 Testnet Report" in text
    assert "ticket-g2" in text
    assert "DAENB1" in text
    assert "pos-g2" in text
    assert "rec-g2" in text
