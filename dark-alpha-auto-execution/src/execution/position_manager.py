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

from .binance_testnet_broker import LiveOrderAck
from .paper_broker import Fill

log = structlog.get_logger(__name__)


class PositionManager:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> Path | None:
        return self._db_path

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

    def update_ticket_status(
        self,
        ticket_id: str,
        status: str,
        reject_reason: str | None = None,
    ) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                UPDATE execution_tickets
                   SET status=?,
                       decided_at=?,
                       reject_reason=?
                 WHERE ticket_id=?
                """,
                (
                    status,
                    datetime.now(tz=UTC).isoformat(),
                    reject_reason,
                    ticket_id,
                ),
            )
            conn.commit()

    def record_live_order_ack(self, ticket: ExecutionTicket, ack: LiveOrderAck) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO orders
                    (order_id, ticket_id, exchange_order_id, side, type, symbol,
                     price, quantity, status, submitted_at, fill_price, fill_quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    ack.client_order_id,
                    ticket.ticket_id,
                    ack.exchange_order_id,
                    ack.side.lower(),
                    ack.type,
                    ack.symbol,
                    ack.price,
                    ack.quantity,
                    ack.status.lower(),
                    datetime.now(tz=UTC).isoformat(),
                ),
            )
            conn.execute(
                """
                UPDATE order_idempotency
                   SET status='submitted',
                       updated_at=?
                 WHERE client_order_id=?
                """,
                (datetime.now(tz=UTC).isoformat(), ack.client_order_id),
            )
            conn.commit()

    def apply_live_order_update(
        self,
        *,
        ticket: ExecutionTicket,
        order_role: str,
        client_order_id: str,
        cumulative_filled: float,
        fill_delta: float,
        average_price: float | None,
        local_status: str,
    ) -> None:
        """Apply a live exchange order update to local position lifecycle.

        `cumulative_filled` is exchange-reported executed quantity. `fill_delta`
        is computed by the order sync layer from the previous local value so
        repeated polling is idempotent.
        """
        if cumulative_filled <= 0 and fill_delta <= 0:
            return
        if order_role == "entry":
            self._apply_live_entry_fill(
                ticket=ticket,
                cumulative_filled=cumulative_filled,
                average_price=average_price or ticket.entry_price,
                local_status=local_status,
            )
            return
        if order_role in {"stop", "take_profit", "emergency_close"}:
            self._apply_live_exit_fill(
                ticket=ticket,
                order_role=order_role,
                client_order_id=client_order_id,
                fill_delta=fill_delta,
                average_price=average_price,
                local_status=local_status,
            )

    def _apply_live_entry_fill(
        self,
        *,
        ticket: ExecutionTicket,
        cumulative_filled: float,
        average_price: float,
        local_status: str,
    ) -> None:
        status = "open" if _nearly_gte(cumulative_filled, ticket.quantity) else "partial"
        with get_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT position_id
                  FROM positions
                 WHERE ticket_id=?
                   AND shadow_mode=0
                   AND status IN ('pending','open','partial')
                 ORDER BY opened_at DESC
                 LIMIT 1
                """,
                (ticket.ticket_id,),
            ).fetchone()
            if row is None:
                position_id = str(ULID())
                conn.execute(
                    """
                    INSERT INTO positions
                        (position_id, ticket_id, symbol, direction, status,
                         entry_price, quantity, filled_quantity, stop_price,
                         take_profit_price, opened_at, fees_usd, shadow_mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                    """,
                    (
                        position_id,
                        ticket.ticket_id,
                        ticket.symbol,
                        ticket.direction,
                        status,
                        average_price,
                        ticket.quantity,
                        cumulative_filled,
                        ticket.stop_price,
                        ticket.take_profit_price,
                        datetime.now(tz=UTC).isoformat(),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE positions
                       SET status=?,
                           entry_price=?,
                           filled_quantity=?,
                           opened_at=COALESCE(opened_at, ?)
                     WHERE position_id=?
                    """,
                    (
                        status,
                        average_price,
                        cumulative_filled,
                        datetime.now(tz=UTC).isoformat(),
                        row["position_id"],
                    ),
                )
            if local_status == "filled":
                conn.execute(
                    "UPDATE execution_tickets SET status='filled' WHERE ticket_id=?",
                    (ticket.ticket_id,),
                )
            conn.commit()
        log.info(
            "position.live_entry_updated",
            ticket_id=ticket.ticket_id,
            status=status,
            filled_quantity=cumulative_filled,
        )

    def _apply_live_exit_fill(
        self,
        *,
        ticket: ExecutionTicket,
        order_role: str,
        client_order_id: str,
        fill_delta: float,
        average_price: float | None,
        local_status: str,
    ) -> None:
        if fill_delta <= 0:
            return
        exit_price = average_price or ticket.take_profit_price or ticket.stop_price
        reason = _exit_reason(order_role, client_order_id)
        with get_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM positions
                 WHERE ticket_id=?
                   AND shadow_mode=0
                   AND status IN ('open','partial')
                 ORDER BY opened_at DESC
                 LIMIT 1
                """,
                (ticket.ticket_id,),
            ).fetchone()
            if row is None:
                log.warning(
                    "position.live_exit_without_position",
                    ticket_id=ticket.ticket_id,
                    order_role=order_role,
                    client_order_id=client_order_id,
                )
                return

            active_qty = float(row["filled_quantity"] or 0.0)
            closed_qty = min(active_qty, fill_delta)
            remaining_qty = max(active_qty - closed_qty, 0.0)
            entry_price = float(row["entry_price"] or ticket.entry_price)
            gross = (
                (exit_price - entry_price) * closed_qty
                if row["direction"] == "long"
                else (entry_price - exit_price) * closed_qty
            )
            existing_gross = float(row["gross_pnl_usd"] or 0.0)
            existing_fees = float(row["fees_usd"] or 0.0)
            net = existing_gross + gross - existing_fees
            if remaining_qty <= 1e-12 or local_status == "filled":
                conn.execute(
                    """
                    UPDATE positions
                       SET status='closed',
                           filled_quantity=0,
                           exit_price=?,
                           closed_at=?,
                           exit_reason=?,
                           gross_pnl_usd=?,
                           net_pnl_usd=?
                     WHERE position_id=?
                    """,
                    (
                        exit_price,
                        datetime.now(tz=UTC).isoformat(),
                        reason,
                        existing_gross + gross,
                        net,
                        row["position_id"],
                    ),
                )
                conn.execute(
                    "UPDATE execution_tickets SET status='closed' WHERE ticket_id=?",
                    (ticket.ticket_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE positions
                       SET status='partial',
                           filled_quantity=?,
                           exit_price=?,
                           exit_reason=?,
                           gross_pnl_usd=?,
                           net_pnl_usd=?
                     WHERE position_id=?
                    """,
                    (
                        remaining_qty,
                        exit_price,
                        reason,
                        existing_gross + gross,
                        net,
                        row["position_id"],
                    ),
                )
            conn.commit()
        log.info(
            "position.live_exit_updated",
            ticket_id=ticket.ticket_id,
            reason=reason,
            closed_quantity=closed_qty,
        )

    def apply_live_symbol_exit(
        self,
        *,
        symbol: str,
        client_order_id: str,
        fill_delta: float,
        average_price: float | None,
        reason: str = "manual",
    ) -> list[str]:
        """Apply an exchange-side symbol exit that is not tied to a planned order.

        Emergency flatten orders are created outside the original bracket and do
        not have an `order_idempotency` row. User-data stream fills for those
        orders still need to close local live positions without relying on a
        later reconciliation repair.
        """
        if fill_delta <= 0:
            return []
        normalized = _normalize_symbol(symbol)
        remaining_fill = fill_delta
        closed_position_ids: list[str] = []
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                  FROM positions
                 WHERE shadow_mode=0
                   AND status IN ('open','partial')
                 ORDER BY opened_at, position_id
                """
            ).fetchall()
            for row in rows:
                if _normalize_symbol(str(row["symbol"])) != normalized:
                    continue
                if remaining_fill <= 1e-12:
                    break

                active_qty = float(row["filled_quantity"] or 0.0)
                closed_qty = min(active_qty, remaining_fill)
                remaining_fill -= closed_qty
                exit_price = average_price or float(row["entry_price"] or 0.0)
                entry_price = float(row["entry_price"] or 0.0)
                gross = (
                    (exit_price - entry_price) * closed_qty
                    if row["direction"] == "long"
                    else (entry_price - exit_price) * closed_qty
                )
                existing_gross = float(row["gross_pnl_usd"] or 0.0)
                existing_fees = float(row["fees_usd"] or 0.0)
                net = existing_gross + gross - existing_fees
                remaining_position_qty = max(active_qty - closed_qty, 0.0)
                if remaining_position_qty <= 1e-12:
                    conn.execute(
                        """
                        UPDATE positions
                           SET status='closed',
                               filled_quantity=0,
                               exit_price=?,
                               closed_at=?,
                               exit_reason=?,
                               gross_pnl_usd=?,
                               net_pnl_usd=?
                         WHERE position_id=?
                        """,
                        (
                            exit_price,
                            datetime.now(tz=UTC).isoformat(),
                            reason,
                            existing_gross + gross,
                            net,
                            row["position_id"],
                        ),
                    )
                    if row["ticket_id"]:
                        conn.execute(
                            "UPDATE execution_tickets SET status='closed' WHERE ticket_id=?",
                            (row["ticket_id"],),
                        )
                    closed_position_ids.append(str(row["position_id"]))
                else:
                    conn.execute(
                        """
                        UPDATE positions
                           SET status='partial',
                               filled_quantity=?,
                               exit_price=?,
                               exit_reason=?,
                               gross_pnl_usd=?,
                               net_pnl_usd=?
                         WHERE position_id=?
                        """,
                        (
                            remaining_position_qty,
                            exit_price,
                            reason,
                            existing_gross + gross,
                            net,
                            row["position_id"],
                        ),
                    )
            conn.commit()
        log.info(
            "position.live_symbol_exit_updated",
            symbol=symbol,
            client_order_id=client_order_id,
            closed_positions=len(closed_position_ids),
        )
        return closed_position_ids

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

    def create_pending_position(self, ticket: ExecutionTicket) -> str:
        position_id = str(ULID())
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO positions
                    (position_id, ticket_id, symbol, direction, status,
                     entry_price, quantity, filled_quantity, stop_price,
                     take_profit_price, opened_at, fees_usd, shadow_mode)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, 0, ?, ?, NULL, 0, ?)
                """,
                (
                    position_id,
                    ticket.ticket_id,
                    ticket.symbol,
                    ticket.direction,
                    ticket.entry_price,
                    ticket.quantity,
                    ticket.stop_price,
                    ticket.take_profit_price,
                    1 if ticket.shadow_mode else 0,
                ),
            )
            conn.commit()
        log.info(
            "position.pending",
            position_id=position_id,
            ticket_id=ticket.ticket_id,
            symbol=ticket.symbol,
        )
        return position_id

    def fill_pending_position(self, position_id: str, ticket: ExecutionTicket, fill: Fill) -> None:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT status FROM positions WHERE position_id=?",
                (position_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown position_id {position_id}")
            if row["status"] != "pending":
                raise ValueError(f"position {position_id} is not pending")

            conn.execute(
                """
                UPDATE positions
                   SET status='open',
                       entry_price=?,
                       filled_quantity=?,
                       opened_at=?,
                       fees_usd=?
                 WHERE position_id=?
                """,
                (
                    fill.price,
                    fill.quantity,
                    datetime.now(tz=UTC).isoformat(),
                    fill.fee_usd,
                    position_id,
                ),
            )
            conn.execute(
                "UPDATE execution_tickets SET status='filled' WHERE ticket_id=?",
                (ticket.ticket_id,),
            )
            conn.commit()
        log.info(
            "position.entry_filled",
            position_id=position_id,
            ticket_id=ticket.ticket_id,
            symbol=ticket.symbol,
            price=fill.price,
        )

    def expire_pending_position(self, position_id: str, ticket_id: str, reason: str = "ttl_expired") -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                UPDATE positions
                   SET status='cancelled',
                       closed_at=?,
                       exit_reason=?
                 WHERE position_id=? AND status='pending'
                """,
                (datetime.now(tz=UTC).isoformat(), reason, position_id),
            )
            conn.execute(
                "UPDATE execution_tickets SET status='expired', reject_reason=? WHERE ticket_id=?",
                (reason, ticket_id),
            )
            conn.commit()
        log.info("position.expired", position_id=position_id, ticket_id=ticket_id, reason=reason)

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


def _nearly_gte(left: float, right: float) -> bool:
    return left + 1e-12 >= right


def _exit_reason(order_role: str, client_order_id: str) -> str:
    if order_role == "take_profit":
        return "take_profit"
    if order_role == "stop":
        return "stop_loss"
    if order_role == "emergency_close" or client_order_id.startswith("DACLOSE"):
        return "manual"
    return "invalidation"


def _normalize_symbol(symbol: str) -> str:
    for suffix in ("-PERP", "_PERP"):
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol
