"""Weekly report — aggregates daily snapshots into a Markdown report.

Output: reports/weekly-YYYY-WW.md
"""

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import structlog

from storage.db import get_db

log = structlog.get_logger(__name__)

_REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "reports"))


def generate(week_ending: date | None = None, db_path: Path | None = None) -> Path:
    """Generate a weekly report ending on `week_ending` (inclusive). Returns path."""
    end = week_ending or datetime.now(tz=UTC).date()
    start = end - timedelta(days=6)
    iso_year, iso_week, _ = end.isocalendar()

    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT date, starting_equity, ending_equity, trade_count,
                   win_count, loss_count, gross_pnl, fees, net_pnl
              FROM daily_snapshots
             WHERE date BETWEEN ? AND ?
             ORDER BY date
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    lines: list[str] = []
    lines.append(f"# Shadow Mode Weekly Report — {iso_year}-W{iso_week:02d}")
    lines.append("")
    lines.append(f"Window: **{start.isoformat()} → {end.isoformat()}** (UTC)")
    lines.append("")

    if not rows:
        lines.append("> No daily snapshots for this window. Run `dark-alpha snapshot` daily.")
    else:
        starting_eq = float(rows[0]["starting_equity"])
        ending_eq = float(rows[-1]["ending_equity"])
        total_trades = sum(int(r["trade_count"]) for r in rows)
        total_wins = sum(int(r["win_count"]) for r in rows)
        total_losses = sum(int(r["loss_count"]) for r in rows)
        total_gross = sum(float(r["gross_pnl"]) for r in rows)
        total_fees = sum(float(r["fees"]) for r in rows)
        total_net = sum(float(r["net_pnl"]) for r in rows)
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
        ret_pct = ((ending_eq - starting_eq) / starting_eq * 100) if starting_eq else 0.0

        lines += [
            "## Summary",
            "",
            f"- Starting equity: **{starting_eq:,.2f} USD**",
            f"- Ending equity:   **{ending_eq:,.2f} USD**",
            f"- Return:          **{ret_pct:+.2f}%**",
            f"- Net P&L:         **{total_net:+,.2f} USD** (gross {total_gross:+,.2f}, fees {total_fees:,.2f})",
            f"- Trades:          **{total_trades}** (W: {total_wins} / L: {total_losses}, win rate {win_rate:.1f}%)",
            "",
            "## Daily breakdown",
            "",
            "| Date | Trades | Wins | Losses | Gross | Fees | Net | Equity |",
            "|------|-------:|-----:|-------:|------:|-----:|----:|-------:|",
        ]
        for r in rows:
            lines.append(
                f"| {r['date']} | {r['trade_count']} | {r['win_count']} | "
                f"{r['loss_count']} | {r['gross_pnl']:+,.2f} | {r['fees']:,.2f} | "
                f"{r['net_pnl']:+,.2f} | {r['ending_equity']:,.2f} |"
            )

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = _REPORTS_DIR / f"weekly-{iso_year}-W{iso_week:02d}.md"
    out.write_text("\n".join(lines) + "\n")
    log.info("reporting.weekly_written", path=str(out), rows=len(rows))
    return out
