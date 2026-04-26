"""Position evaluator — checks pending/open shadow positions against market price.

Pending entries fill only when price touches the entry before TTL expiry.
Open positions close when stop or take-profit is crossed.

One tick does:
  1. load all pending/open positions from the DB
  2. expire overdue pending entries
  3. fetch latest price for each active position
  4. fill entries or close exits when price crosses a level
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from market_data.binance_public import BinancePublicClient, PriceSource
from storage.db import get_db
from strategy.schemas import ExecutionTicket

from .paper_broker import PaperBroker
from .position_manager import PositionManager

log = structlog.get_logger(__name__)


@dataclass
class EvalResult:
    position_id: str
    symbol: str
    triggered: str | None  # 'entry_fill' | 'entry_expired' | 'stop_loss' | 'take_profit' | None
    mark_price: float | None


@dataclass
class _OpenPosition:
    position_id: str
    ticket_id: str
    symbol: str
    direction: str
    status: str
    entry_price: float | None
    stop_price: float | None
    take_profit_price: float | None


class PositionEvaluator:
    def __init__(
        self,
        price_source: PriceSource | None = None,
        broker: PaperBroker | None = None,
        manager: PositionManager | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._prices = price_source or BinancePublicClient()
        self._broker = broker or PaperBroker()
        self._manager = manager or PositionManager(db_path=db_path)
        self._db_path = db_path

    def tick(self) -> list[EvalResult]:
        """One evaluation pass. Returns what happened for each open position."""
        results: list[EvalResult] = []
        for pos in self._load_open_positions():
            if pos.status == "pending":
                ticket = self._load_ticket(pos.ticket_id)
                if ticket is None:
                    log.warning("evaluator.ticket_missing", ticket_id=pos.ticket_id)
                    continue
                if self._is_expired(ticket):
                    self._manager.expire_pending_position(pos.position_id, pos.ticket_id)
                    results.append(EvalResult(pos.position_id, pos.symbol, "entry_expired", None))
                    continue
                mark = self._prices.last_price(pos.symbol)
                if mark is None:
                    results.append(EvalResult(pos.position_id, pos.symbol, None, None))
                    continue
                if self._entry_touched(ticket, mark):
                    fill = self._broker.simulate_entry(ticket)
                    self._manager.fill_pending_position(pos.position_id, ticket, fill)
                    log.info(
                        "evaluator.entry_filled",
                        position_id=pos.position_id,
                        symbol=pos.symbol,
                        mark=mark,
                    )
                    results.append(EvalResult(pos.position_id, pos.symbol, "entry_fill", mark))
                    continue
                results.append(EvalResult(pos.position_id, pos.symbol, None, mark))
                continue

            mark = self._prices.last_price(pos.symbol)
            if mark is None:
                results.append(EvalResult(pos.position_id, pos.symbol, None, None))
                continue
            reason = self._check_exit_trigger(pos, mark)
            if reason is None:
                results.append(EvalResult(pos.position_id, pos.symbol, None, mark))
                continue

            ticket = self._load_ticket(pos.ticket_id)
            if ticket is None:
                log.warning("evaluator.ticket_missing", ticket_id=pos.ticket_id)
                continue
            fill = self._broker.simulate_exit(ticket, reason=reason, mark_price=mark)
            self._manager.close_position(pos.position_id, fill, reason=reason)
            log.info(
                "evaluator.position_closed",
                position_id=pos.position_id,
                symbol=pos.symbol,
                reason=reason,
                mark=mark,
            )
            results.append(EvalResult(pos.position_id, pos.symbol, reason, mark))
        return results

    @staticmethod
    def _check_exit_trigger(pos: _OpenPosition, mark: float) -> str | None:
        stop = pos.stop_price
        tp = pos.take_profit_price
        if pos.direction == "long":
            if stop is not None and mark <= stop:
                return "stop_loss"
            if tp is not None and mark >= tp:
                return "take_profit"
        else:
            if stop is not None and mark >= stop:
                return "stop_loss"
            if tp is not None and mark <= tp:
                return "take_profit"
        return None

    def _load_open_positions(self) -> list[_OpenPosition]:
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT position_id, ticket_id, symbol, direction,
                       status, entry_price, stop_price, take_profit_price
                  FROM positions
                 WHERE status IN ('pending','open')
                   AND shadow_mode=1
                """
            ).fetchall()
        return [
            _OpenPosition(
                position_id=r["position_id"],
                ticket_id=r["ticket_id"],
                symbol=r["symbol"],
                direction=r["direction"],
                status=r["status"],
                entry_price=r["entry_price"],
                stop_price=r["stop_price"],
                take_profit_price=r["take_profit_price"],
            )
            for r in rows
        ]

    def _load_ticket(self, ticket_id: str) -> ExecutionTicket | None:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT payload FROM execution_tickets WHERE ticket_id=?",
                (ticket_id,),
            ).fetchone()
        if row is None:
            return None
        return ExecutionTicket.model_validate_json(row["payload"])

    @staticmethod
    def _entry_touched(ticket: ExecutionTicket, mark: float) -> bool:
        if ticket.direction == "long":
            return mark <= ticket.entry_price
        return mark >= ticket.entry_price

    @staticmethod
    def _is_expired(ticket: ExecutionTicket, now: datetime | None = None) -> bool:
        now = now or datetime.now(tz=UTC)
        created = datetime.fromisoformat(ticket.created_at.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        ttl_minutes = _ticket_ttl_minutes(ticket)
        return created.astimezone(UTC) + timedelta(minutes=ttl_minutes) <= now


def _ticket_ttl_minutes(ticket: ExecutionTicket) -> int:
    metadata = ticket.metadata.get("event_metadata")
    if isinstance(metadata, dict):
        raw = metadata.get("ttl_minutes")
    else:
        raw = ticket.metadata.get("ttl_minutes")
    try:
        ttl = int(float(raw)) if raw is not None else 15
    except (TypeError, ValueError):
        ttl = 15
    return max(ttl, 1)
