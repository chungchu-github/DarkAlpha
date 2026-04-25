"""Position manager — persists tickets, open positions, trades, and equity.

Responsibilities:
  - store ExecutionTicket in execution_tickets
  - open / close position rows in the positions table
  - compute realized PnL and fees on close
  - write paired entry+exit rows to trades
  - emit equity_snapshots for reporting
"""

from datetime import UTC, datetime
from pathlib import Path

import structlog
from ulid import ULID

from storage.db import get_db
from strategy.schemas import ExecutionTicket, Rejection

from .paper_broker import Fill

log = structlog.get_logger(__name__)


class PositionManager:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Ticket persistence
    # ------------------------------------------------------------------

    def persist_ticket(self, ticket: ExecutionTicket, status: str = "created") -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO execution_tickets
                    (ticket_id, source_event_id, status, shadow_mode,
                     payload, created_at, decided_at, reject_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    ticket.ticket_id,
                    ticket.source_event_id,
                    status,
                    1 if ticket.shadow_mode else 0,
                    ticket.model_dump_json(),
                    ticket.created_at,
                    datetime.now(tz=UTC).isoformat(),
                ),
            )
            conn.commit()

    def persist_rejection(
        self,
        rejection: Rejection,
        shadow_mode: bool = True,
    ) -> None:
        ticket_id = str(ULID())
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO execution_tickets
                    (ticket_id, source_event_id, status, shadow_mode,
                     payload, created_at, decided_at, reject_reason)
                VALUES (?, ?, 'rejected', ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id,
                    rejection.source_event_id,
                    1 if shadow_mode else 0,
                    rejection.model_dump_json(),
                    datetime.now(tz=UTC).isoformat(),
                    datetime.now(tz=UTC).isoformat(),
                    f"{rejection.stage}:{rejection.reason}",
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------

    def open_position(self, ticket: ExecutionTicket, fill: Fill) -> str:
        position_id = str(ULID())
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO positions
                    (position_id, ticket_id, symbol, direction, status,
                     entry_price, quantity, filled_quantity, stop_price,
                     take_profit_price, opened_at, fees_usd, shadow_mode)
                VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    ticket.ticket_id,
                    ticket.symbol,
                    ticket.direction,
                    fill.price,
                    ticket.quantity,
                    fill.quantity,
                    ticket.stop_price,
                    ticket.take_profit_price,
                    datetime.now(tz=UTC).isoformat(),
                    fill.fee_usd,
                    1 if ticket.shadow_mode else 0,
                ),
            )
            conn.execute(
                "UPDATE execution_tickets SET status='filled' WHERE ticket_id=?",
                (ticket.ticket_id,),
            )
            conn.commit()
        log.info(
            "position.opened",
            position_id=position_id,
            ticket_id=ticket.ticket_id,
            symbol=ticket.symbol,
            price=fill.price,
        )
        return position_id

    def close_position(
        self,
        position_id: str,
        exit_fill: Fill,
        reason: str,
    ) -> dict[str, float]:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE position_id=?",
                (position_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown position_id {position_id}")

            entry_price = row["entry_price"]
            qty = row["quantity"]
            direction = row["direction"]
            entry_fee = row["fees_usd"] or 0.0

            gross = (
                (exit_fill.price - entry_price) * qty
                if direction == "long"
                else (entry_price - exit_fill.price) * qty
            )
            fees = entry_fee + exit_fill.fee_usd
            net = gross - fees

            conn.execute(
                """
                UPDATE positions
                SET status='closed',
                    exit_price=?, closed_at=?, exit_reason=?,
                    gross_pnl_usd=?, fees_usd=?, net_pnl_usd=?
                WHERE position_id=?
                """,
                (
                    exit_fill.price,
                    datetime.now(tz=UTC).isoformat(),
                    reason,
                    gross,
                    fees,
                    net,
                    position_id,
                ),
            )
            conn.execute(
                "UPDATE execution_tickets SET status='closed' WHERE ticket_id=?",
                (row["ticket_id"],),
            )
            conn.commit()

        log.info(
            "position.closed",
            position_id=position_id,
            reason=reason,
            gross_pnl_usd=gross,
            fees_usd=fees,
            net_pnl_usd=net,
        )
        return {"gross_pnl_usd": gross, "fees_usd": fees, "net_pnl_usd": net}

    # ------------------------------------------------------------------
    # Equity
    # ------------------------------------------------------------------

    def snapshot_equity(
        self,
        equity_usd: float,
        mode: str,
        gate: str,
        realized: float = 0.0,
        unrealized: float = 0.0,
    ) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO equity_snapshots (equity_usd, realized, unrealized, mode, gate)
                VALUES (?, ?, ?, ?, ?)
                """,
                (equity_usd, realized, unrealized, mode, gate),
            )
            conn.commit()

    def current_equity(self, starting_equity: float) -> float:
        """Compute equity = starting + sum(net_pnl_usd of closed positions)."""
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(net_pnl_usd), 0) AS pnl FROM positions WHERE status='closed'"
            ).fetchone()
        realized = float(row["pnl"] or 0.0)
        return starting_equity + realized
