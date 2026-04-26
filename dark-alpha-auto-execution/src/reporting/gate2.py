"""Gate 2 testnet report generation."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from storage.db import get_db

_REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "reports"))


def write_report(
    *,
    ticket_id: str | None = None,
    event_id: str | None = None,
    db_path: Path | None = None,
    reports_dir: Path | None = None,
) -> Path:
    row = _load_ticket(ticket_id=ticket_id, event_id=event_id, db_path=db_path)
    if row is None:
        raise ValueError("gate2_ticket_not_found")

    ticket_id = str(row["ticket_id"])
    lines = _render_report(row, db_path=db_path)
    out_dir = reports_dir or _REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"gate2-{ticket_id}.md"
    out.write_text("\n".join(lines) + "\n")
    return out


def _load_ticket(
    *,
    ticket_id: str | None,
    event_id: str | None,
    db_path: Path | None,
) -> object | None:
    with get_db(db_path) as conn:
        if ticket_id:
            return conn.execute(
                """
                SELECT t.*, e.symbol AS event_symbol, e.payload AS event_payload
                  FROM execution_tickets t
                  LEFT JOIN setup_events e ON e.event_id = t.source_event_id
                 WHERE t.ticket_id=?
                """,
                (ticket_id,),
            ).fetchone()
        if event_id:
            return conn.execute(
                """
                SELECT t.*, e.symbol AS event_symbol, e.payload AS event_payload
                  FROM execution_tickets t
                  LEFT JOIN setup_events e ON e.event_id = t.source_event_id
                 WHERE t.source_event_id=?
                 ORDER BY t.created_at DESC
                 LIMIT 1
                """,
                (event_id,),
            ).fetchone()
        return conn.execute(
            """
            SELECT t.*, e.symbol AS event_symbol, e.payload AS event_payload
              FROM execution_tickets t
              LEFT JOIN setup_events e ON e.event_id = t.source_event_id
             WHERE t.shadow_mode=0
             ORDER BY t.created_at DESC
             LIMIT 1
            """
        ).fetchone()


def _render_report(ticket: object, *, db_path: Path | None) -> list[str]:
    with get_db(db_path) as conn:
        orders = conn.execute(
            """
            SELECT order_id, exchange_order_id, side, type, symbol, price,
                   quantity, status, submitted_at, filled_at, fill_price,
                   fill_quantity
              FROM orders
             WHERE ticket_id=?
             ORDER BY submitted_at, order_id
            """,
            (ticket["ticket_id"],),
        ).fetchall()
        positions = conn.execute(
            """
            SELECT position_id, symbol, direction, status, entry_price, exit_price,
                   quantity, filled_quantity, stop_price, take_profit_price,
                   exit_reason, gross_pnl_usd, fees_usd, net_pnl_usd
              FROM positions
             WHERE ticket_id=?
             ORDER BY opened_at, position_id
            """,
            (ticket["ticket_id"],),
        ).fetchall()
        reconciliation = conn.execute(
            """
            SELECT run_id, status, details, created_at
              FROM reconciliation_runs
             ORDER BY created_at DESC
             LIMIT 1
            """
        ).fetchone()

    lines = [
        f"# Gate 2 Testnet Report - {ticket['ticket_id']}",
        "",
        f"Generated at: {datetime.now(tz=UTC).isoformat()}",
        "",
        "## Ticket",
        "",
        f"- Ticket ID: `{ticket['ticket_id']}`",
        f"- Source event: `{ticket['source_event_id']}`",
        f"- Status: `{ticket['status']}`",
        f"- Shadow mode: `{ticket['shadow_mode']}`",
        f"- Created at: `{ticket['created_at']}`",
        "",
        "## Orders",
        "",
    ]

    if not orders:
        lines.append("- No orders recorded.")
    else:
        lines += [
            "| Client Order ID | Exchange ID | Symbol | Side | Type | Status | Price | Qty | Filled | Avg Fill |",
            "|---|---|---|---|---|---|---:|---:|---:|---:|",
        ]
        for order in orders:
            lines.append(
                f"| `{order['order_id']}` | `{order['exchange_order_id'] or ''}` | "
                f"{order['symbol']} | {order['side']} | {order['type']} | {order['status']} | "
                f"{_fmt(order['price'])} | {_fmt(order['quantity'])} | "
                f"{_fmt(order['fill_quantity'])} | {_fmt(order['fill_price'])} |"
            )

    lines += ["", "## Positions", ""]
    if not positions:
        lines.append("- No live position rows recorded.")
    else:
        lines += [
            "| Position ID | Symbol | Direction | Status | Entry | Exit | Qty | Filled | Exit Reason | Net PnL |",
            "|---|---|---|---|---:|---:|---:|---:|---|---:|",
        ]
        for position in positions:
            lines.append(
                f"| `{position['position_id']}` | {position['symbol']} | {position['direction']} | "
                f"{position['status']} | {_fmt(position['entry_price'])} | {_fmt(position['exit_price'])} | "
                f"{_fmt(position['quantity'])} | {_fmt(position['filled_quantity'])} | "
                f"{position['exit_reason'] or ''} | {_fmt(position['net_pnl_usd'])} |"
            )

    lines += ["", "## Latest Reconciliation", ""]
    if reconciliation is None:
        lines.append("- No reconciliation run recorded.")
    else:
        lines += [
            f"- Run ID: `{reconciliation['run_id']}`",
            f"- Status: `{reconciliation['status']}`",
            f"- Created at: `{reconciliation['created_at']}`",
            "",
            "```json",
            str(reconciliation["details"] or "{}"),
            "```",
        ]

    return lines


def _fmt(value: object) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.8f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)
