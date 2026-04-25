"""Daily snapshot writer.

Collapses the day into one row in `daily_snapshots`:
  - starting_equity: previous snapshot's ending_equity, or config starting_equity
  - ending_equity:   starting + sum(net_pnl of positions closed on that date)
  - trade_count / win_count / loss_count / gross_pnl / fees / net_pnl
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import structlog

from storage.db import get_db
from strategy.config import main_config, sizer_config

log = structlog.get_logger(__name__)


@dataclass
class DailyStats:
    date: str
    starting_equity: float
    ending_equity: float
    trade_count: int
    win_count: int
    loss_count: int
    gross_pnl: float
    fees: float
    net_pnl: float
    mode: str
    gate: str


def write_snapshot(target_date: date | None = None, db_path: Path | None = None) -> DailyStats:
    """Compute and persist the daily_snapshots row for `target_date` (UTC)."""
    d = target_date or (datetime.now(tz=UTC).date() - timedelta(days=1))
    date_str = d.isoformat()
    mode = str(main_config().get("mode", "shadow"))
    gate = f"gate{main_config().get('gate', 1)}"
    starting_equity = _starting_equity(d, db_path)

    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT gross_pnl_usd, fees_usd, net_pnl_usd
              FROM positions
             WHERE status='closed'
               AND substr(closed_at,1,10)=?
            """,
            (date_str,),
        ).fetchall()

        trade_count = len(rows)
        wins = sum(1 for r in rows if (r["net_pnl_usd"] or 0.0) > 0)
        losses = sum(1 for r in rows if (r["net_pnl_usd"] or 0.0) < 0)
        gross = sum(float(r["gross_pnl_usd"] or 0.0) for r in rows)
        fees = sum(float(r["fees_usd"] or 0.0) for r in rows)
        net = sum(float(r["net_pnl_usd"] or 0.0) for r in rows)
        ending = starting_equity + net

        conn.execute(
            """
            INSERT OR REPLACE INTO daily_snapshots
                (date, starting_equity, ending_equity, trade_count, win_count,
                 loss_count, gross_pnl, fees, net_pnl, max_drawdown_intraday,
                 mode, gate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (date_str, starting_equity, ending, trade_count, wins, losses,
             gross, fees, net, mode, gate),
        )
        conn.commit()

    stats = DailyStats(
        date=date_str,
        starting_equity=starting_equity,
        ending_equity=ending,
        trade_count=trade_count,
        win_count=wins,
        loss_count=losses,
        gross_pnl=gross,
        fees=fees,
        net_pnl=net,
        mode=mode,
        gate=gate,
    )
    log.info("reporting.daily_snapshot_written", **stats.__dict__)
    return stats


def _starting_equity(d: date, db_path: Path | None) -> float:
    """Yesterday's ending_equity, falling back to sizer starting_equity_usd."""
    prev = (d - timedelta(days=1)).isoformat()
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT ending_equity FROM daily_snapshots WHERE date=?",
            (prev,),
        ).fetchone()
    if row is not None and row["ending_equity"] is not None:
        return float(row["ending_equity"])
    gate = f"gate{main_config().get('gate', 1)}"
    return float(sizer_config(gate).get("starting_equity_usd", 10_000.0))
