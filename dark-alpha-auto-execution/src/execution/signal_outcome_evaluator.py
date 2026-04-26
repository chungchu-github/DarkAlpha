"""Post-signal outcome evaluator.

Fills pending 5m / 15m / 1h / 4h rows in signal_outcomes once their horizon is
due. This is deliberately simple for Week 2: it uses the current mark at the
evaluation time. Week 3 can upgrade MFE/MAE to full path-based calculations.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from market_data.binance_public import BinancePublicClient, PriceSource
from storage.db import get_db

log = structlog.get_logger(__name__)

_HORIZON_DELTAS = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}


@dataclass
class OutcomeEvalResult:
    event_id: str
    horizon: str
    symbol: str
    status: str
    mark_price: float | None


@dataclass
class _PendingOutcome:
    event_id: str
    horizon: str
    timestamp: str
    symbol: str
    direction: str | None
    entry_price: float | None
    stop_price: float | None


class SignalOutcomeEvaluator:
    def __init__(
        self,
        price_source: PriceSource | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._prices = price_source or BinancePublicClient()
        self._db_path = db_path

    def tick(self, now: datetime | None = None) -> list[OutcomeEvalResult]:
        now = now or datetime.now(tz=UTC)
        results: list[OutcomeEvalResult] = []
        for outcome in self._load_due(now):
            mark = self._prices.last_price(outcome.symbol)
            if mark is None:
                self._mark_failed(outcome, now)
                results.append(
                    OutcomeEvalResult(outcome.event_id, outcome.horizon, outcome.symbol, "failed", None)
                )
                continue

            return_pct, r_multiple = self._measure(outcome, mark)
            max_favorable = max(return_pct, 0.0) if return_pct is not None else None
            max_adverse = min(return_pct, 0.0) if return_pct is not None else None
            self._mark_observed(
                outcome,
                now=now,
                mark_price=mark,
                return_pct=return_pct,
                r_multiple=r_multiple,
                max_favorable_pct=max_favorable,
                max_adverse_pct=max_adverse,
            )
            results.append(
                OutcomeEvalResult(outcome.event_id, outcome.horizon, outcome.symbol, "observed", mark)
            )
        if results:
            log.info("signal_outcomes.evaluated", count=len(results))
        return results

    def _load_due(self, now: datetime) -> list[_PendingOutcome]:
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT o.event_id, o.horizon, j.timestamp, j.symbol, j.direction,
                       j.entry_price, j.stop_price
                  FROM signal_outcomes o
                  JOIN signal_journal j ON j.event_id = o.event_id
                 WHERE o.status='pending'
                """
            ).fetchall()
        due: list[_PendingOutcome] = []
        for row in rows:
            horizon = row["horizon"]
            delta = _HORIZON_DELTAS.get(horizon)
            if delta is None:
                continue
            event_ts = self._parse_ts(row["timestamp"])
            if event_ts + delta <= now:
                due.append(
                    _PendingOutcome(
                        event_id=row["event_id"],
                        horizon=horizon,
                        timestamp=row["timestamp"],
                        symbol=row["symbol"],
                        direction=row["direction"],
                        entry_price=row["entry_price"],
                        stop_price=row["stop_price"],
                    )
                )
        return due

    @staticmethod
    def _parse_ts(raw: str) -> datetime:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _measure(outcome: _PendingOutcome, mark: float) -> tuple[float | None, float | None]:
        if not outcome.entry_price or not outcome.direction:
            return None, None
        if outcome.direction == "short":
            return_pct = (outcome.entry_price - mark) / outcome.entry_price
        else:
            return_pct = (mark - outcome.entry_price) / outcome.entry_price

        if not outcome.stop_price or outcome.stop_price == outcome.entry_price:
            return return_pct, None
        risk_per_unit = abs(outcome.entry_price - outcome.stop_price)
        pnl_per_unit = (
            outcome.entry_price - mark if outcome.direction == "short" else mark - outcome.entry_price
        )
        return return_pct, pnl_per_unit / risk_per_unit

    def _mark_failed(self, outcome: _PendingOutcome, now: datetime) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                UPDATE signal_outcomes
                   SET status='failed', observed_at=?
                 WHERE event_id=? AND horizon=?
                """,
                (now.isoformat(), outcome.event_id, outcome.horizon),
            )
            conn.commit()

    def _mark_observed(
        self,
        outcome: _PendingOutcome,
        *,
        now: datetime,
        mark_price: float,
        return_pct: float | None,
        r_multiple: float | None,
        max_favorable_pct: float | None,
        max_adverse_pct: float | None,
    ) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                UPDATE signal_outcomes
                   SET status='observed',
                       observed_at=?,
                       mark_price=?,
                       return_pct=?,
                       r_multiple=?,
                       max_favorable_pct=?,
                       max_adverse_pct=?
                 WHERE event_id=? AND horizon=?
                """,
                (
                    now.isoformat(),
                    mark_price,
                    return_pct,
                    r_multiple,
                    max_favorable_pct,
                    max_adverse_pct,
                    outcome.event_id,
                    outcome.horizon,
                ),
            )
            conn.commit()
