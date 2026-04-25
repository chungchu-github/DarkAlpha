"""Position evaluator — checks open positions against current mark price.

In shadow mode, entries fill immediately in the paper broker. Exits don't —
we need to poll the market and simulate a fill when the stop or take-profit
price is crossed.

One tick does:
  1. load all open positions from the DB
  2. fetch latest price for each distinct symbol
  3. for each position, check stop / TP crossing rules
  4. if triggered, simulate the exit fill via PaperBroker + close via PositionManager
"""

from dataclasses import dataclass
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
    triggered: str | None  # 'stop_loss' | 'take_profit' | None
    mark_price: float | None


@dataclass
class _OpenPosition:
    position_id: str
    ticket_id: str
    symbol: str
    direction: str
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
            mark = self._prices.last_price(pos.symbol)
            if mark is None:
                results.append(EvalResult(pos.position_id, pos.symbol, None, None))
                continue

            reason = self._check_trigger(pos, mark)
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
    def _check_trigger(pos: _OpenPosition, mark: float) -> str | None:
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
                       stop_price, take_profit_price
                  FROM positions
                 WHERE status='open'
                """
            ).fetchall()
        return [
            _OpenPosition(
                position_id=r["position_id"],
                ticket_id=r["ticket_id"],
                symbol=r["symbol"],
                direction=r["direction"],
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
