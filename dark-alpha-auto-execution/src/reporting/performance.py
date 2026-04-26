"""Shadow performance report by symbol, strategy, and regime."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from storage.db import get_db


@dataclass
class PerformanceBucket:
    key: str
    trades: int
    wins: int
    losses: int
    net_pnl: float
    gross_pnl: float
    fees: float

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100.0) if self.trades else 0.0


def summarize(db_path: Path | None = None) -> dict[str, list[PerformanceBucket]]:
    rows = _closed_position_rows(db_path)
    groups: dict[str, dict[str, dict[str, float | int]]] = {
        "symbol": defaultdict(_empty_stats),
        "strategy": defaultdict(_empty_stats),
        "regime": defaultdict(_empty_stats),
    }

    for row in rows:
        payload = _json(row["payload"])
        regime = str(payload.get("regime") or "unknown")
        strategy = regime
        event_metadata = payload.get("metadata", {}).get("event_metadata")
        if isinstance(event_metadata, dict):
            strategy = str(event_metadata.get("strategy") or regime)

        _add(groups["symbol"][str(row["symbol"])], row)
        _add(groups["strategy"][strategy], row)
        _add(groups["regime"][regime], row)

    return {
        name: [_bucket(key, stats) for key, stats in sorted(group.items())]
        for name, group in groups.items()
    }


def render_markdown(db_path: Path | None = None) -> str:
    grouped = summarize(db_path)
    lines = ["# Shadow Performance Report", ""]
    for section in ("symbol", "strategy", "regime"):
        lines.extend(
            [
                f"## By {section.title()}",
                "",
                "| Key | Trades | Win Rate | Gross | Fees | Net |",
                "|-----|-------:|---------:|------:|-----:|----:|",
            ]
        )
        buckets = grouped[section]
        if not buckets:
            lines.append("| n/a | 0 | 0.0% | +0.00 | 0.00 | +0.00 |")
        for bucket in buckets:
            lines.append(
                f"| {bucket.key} | {bucket.trades} | {bucket.win_rate:.1f}% | "
                f"{bucket.gross_pnl:+,.2f} | {bucket.fees:,.2f} | {bucket.net_pnl:+,.2f} |"
            )
        lines.append("")
    return "\n".join(lines)


def _closed_position_rows(db_path: Path | None):
    with get_db(db_path) as conn:
        return conn.execute(
            """
            SELECT p.symbol, p.gross_pnl_usd, p.fees_usd, p.net_pnl_usd, t.payload
              FROM positions p
              LEFT JOIN execution_tickets t ON t.ticket_id = p.ticket_id
             WHERE p.status='closed'
               AND p.net_pnl_usd IS NOT NULL
            """
        ).fetchall()


def _empty_stats() -> dict[str, float | int]:
    return {"trades": 0, "wins": 0, "losses": 0, "net": 0.0, "gross": 0.0, "fees": 0.0}


def _add(stats: dict[str, float | int], row) -> None:
    net = float(row["net_pnl_usd"] or 0.0)
    stats["trades"] = int(stats["trades"]) + 1
    stats["wins"] = int(stats["wins"]) + (1 if net > 0 else 0)
    stats["losses"] = int(stats["losses"]) + (1 if net < 0 else 0)
    stats["net"] = float(stats["net"]) + net
    stats["gross"] = float(stats["gross"]) + float(row["gross_pnl_usd"] or 0.0)
    stats["fees"] = float(stats["fees"]) + float(row["fees_usd"] or 0.0)


def _bucket(key: str, stats: dict[str, float | int]) -> PerformanceBucket:
    return PerformanceBucket(
        key=key,
        trades=int(stats["trades"]),
        wins=int(stats["wins"]),
        losses=int(stats["losses"]),
        net_pnl=float(stats["net"]),
        gross_pnl=float(stats["gross"]),
        fees=float(stats["fees"]),
    )


def _json(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
