"""Backtest vs shadow comparison report."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backtest.runner import BacktestTrade
from storage.db import get_db


@dataclass(frozen=True)
class CompareRow:
    event_id: str
    symbol: str
    backtest_status: str
    backtest_r: float | None
    shadow_status: str
    shadow_r: float | None
    delta_r: float | None


def compare(trades: list[BacktestTrade], db_path: Path | None = None) -> list[CompareRow]:
    shadow = _shadow_by_event(db_path)
    rows: list[CompareRow] = []
    for trade in trades:
        shadow_row = shadow.get(trade.event_id)
        shadow_status = "missing"
        shadow_r = None
        if shadow_row is not None:
            shadow_status = str(shadow_row["status"])
            shadow_r = _shadow_r(shadow_row)
        delta_r = None
        if trade.r_multiple is not None and shadow_r is not None:
            delta_r = shadow_r - trade.r_multiple
        rows.append(
            CompareRow(
                event_id=trade.event_id,
                symbol=trade.symbol,
                backtest_status=trade.status,
                backtest_r=trade.r_multiple,
                shadow_status=shadow_status,
                shadow_r=shadow_r,
                delta_r=delta_r,
            )
        )
    return rows


def render_markdown(trades: list[BacktestTrade], db_path: Path | None = None) -> str:
    rows = compare(trades, db_path=db_path)
    lines = [
        "# Backtest vs Shadow Report",
        "",
        "| Event | Symbol | Backtest | Backtest R | Shadow | Shadow R | Delta R |",
        "|-------|--------|----------|-----------:|--------|---------:|--------:|",
    ]
    if not rows:
        lines.append("| n/a | n/a | n/a | n/a | n/a | n/a | n/a |")
    for row in rows:
        lines.append(
            f"| {row.event_id} | {row.symbol} | {row.backtest_status} | {_fmt(row.backtest_r)} | "
            f"{row.shadow_status} | {_fmt(row.shadow_r)} | {_fmt(row.delta_r)} |"
        )
    return "\n".join(lines)


def _shadow_by_event(db_path: Path | None):
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT t.source_event_id AS event_id, p.status, p.direction,
                   p.entry_price, p.exit_price, p.stop_price, p.net_pnl_usd
              FROM positions p
              JOIN execution_tickets t ON t.ticket_id = p.ticket_id
            """
        ).fetchall()
    return {row["event_id"]: row for row in rows}


def _shadow_r(row) -> float | None:
    entry = row["entry_price"]
    exit_price = row["exit_price"]
    stop = row["stop_price"]
    if entry is None or exit_price is None or stop is None or entry == stop:
        return None
    pnl_per_unit = (
        float(exit_price) - float(entry)
        if row["direction"] == "long"
        else float(entry) - float(exit_price)
    )
    return pnl_per_unit / abs(float(entry) - float(stop))


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}"
