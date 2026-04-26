"""Basic historical backtest runner.

The runner consumes `signal_journal` rows and a pluggable candle source. It
models the same Gate 3 paper rules used by shadow mode:

- limit entry fills only if price touches entry before TTL expiry
- after entry, stop loss or take profit closes the trade
- if neither exit is touched inside the evaluation window, close at last close
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from storage.db import get_db

_DEFAULT_EXIT_HORIZON = timedelta(hours=4)


@dataclass(frozen=True)
class HistoricalCandle:
    ts: datetime
    high: float
    low: float
    close: float


class HistoricalPriceSource(Protocol):
    def candles(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[HistoricalCandle]: ...


class CsvHistoricalPriceSource:
    """Read candles from `<symbol>.csv` files.

    Required columns: `ts`, `high`, `low`, `close`. `ts` accepts ISO8601.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def candles(self, symbol: str, start: datetime, end: datetime) -> list[HistoricalCandle]:
        path = self._dir / f"{symbol}.csv"
        if not path.exists():
            return []
        out: list[HistoricalCandle] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ts = _parse_ts(str(row.get("ts") or ""))
                if start <= ts <= end:
                    out.append(
                        HistoricalCandle(
                            ts=ts,
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                        )
                    )
        return sorted(out, key=lambda candle: candle.ts)


@dataclass(frozen=True)
class BacktestSignal:
    event_id: str
    timestamp: datetime
    symbol: str
    strategy: str
    direction: str
    entry_price: float
    stop_price: float
    take_profit_price: float | None
    ttl_minutes: int


@dataclass(frozen=True)
class BacktestTrade:
    event_id: str
    symbol: str
    strategy: str
    direction: str
    status: str  # expired | closed | open_ended | no_data
    entry_time: datetime | None
    exit_time: datetime | None
    entry_price: float | None
    exit_price: float | None
    exit_reason: str
    r_multiple: float | None
    return_pct: float | None


@dataclass(frozen=True)
class BacktestSummary:
    trades: int
    entered: int
    expired: int
    wins: int
    losses: int
    avg_r: float


def run_backtest(
    price_source: HistoricalPriceSource,
    *,
    db_path: Path | None = None,
    exit_horizon: timedelta = _DEFAULT_EXIT_HORIZON,
) -> list[BacktestTrade]:
    return [
        _simulate(signal, price_source, exit_horizon=exit_horizon)
        for signal in _load_signals(db_path)
    ]


def summarize(trades: list[BacktestTrade]) -> BacktestSummary:
    entered = [trade for trade in trades if trade.entry_price is not None]
    r_values = [trade.r_multiple for trade in entered if trade.r_multiple is not None]
    wins = sum(1 for value in r_values if value > 0)
    losses = sum(1 for value in r_values if value < 0)
    avg_r = sum(r_values) / len(r_values) if r_values else 0.0
    return BacktestSummary(
        trades=len(trades),
        entered=len(entered),
        expired=sum(1 for trade in trades if trade.status == "expired"),
        wins=wins,
        losses=losses,
        avg_r=avg_r,
    )


def _load_signals(db_path: Path | None) -> list[BacktestSignal]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT event_id, timestamp, symbol, strategy, direction, entry_price,
                   stop_price, take_profit_price, ttl_minutes
              FROM signal_journal
             WHERE direction IN ('long','short')
               AND entry_price IS NOT NULL
               AND stop_price IS NOT NULL
             ORDER BY timestamp
            """
        ).fetchall()

    signals: list[BacktestSignal] = []
    for row in rows:
        signals.append(
            BacktestSignal(
                event_id=row["event_id"],
                timestamp=_parse_ts(row["timestamp"]),
                symbol=row["symbol"],
                strategy=row["strategy"],
                direction=row["direction"],
                entry_price=float(row["entry_price"]),
                stop_price=float(row["stop_price"]),
                take_profit_price=float(row["take_profit_price"])
                if row["take_profit_price"] is not None
                else None,
                ttl_minutes=max(int(row["ttl_minutes"] or 15), 1),
            )
        )
    return signals


def _simulate(
    signal: BacktestSignal,
    price_source: HistoricalPriceSource,
    *,
    exit_horizon: timedelta,
) -> BacktestTrade:
    end = signal.timestamp + exit_horizon
    candles = price_source.candles(signal.symbol, signal.timestamp, end)
    if not candles:
        return _trade(signal, status="no_data", exit_reason="no_data")

    entry_deadline = signal.timestamp + timedelta(minutes=signal.ttl_minutes)
    entry_candle = next(
        (
            candle
            for candle in candles
            if candle.ts <= entry_deadline and _entry_touched(signal, candle)
        ),
        None,
    )
    if entry_candle is None:
        return _trade(signal, status="expired", exit_reason="ttl_expired")

    for candle in candles:
        if candle.ts < entry_candle.ts:
            continue
        exit_reason = _exit_touched(signal, candle)
        if exit_reason is not None:
            exit_price = (
                signal.stop_price if exit_reason == "stop_loss" else signal.take_profit_price
            )
            assert exit_price is not None
            return _trade(
                signal,
                status="closed",
                entry_time=entry_candle.ts,
                exit_time=candle.ts,
                entry_price=signal.entry_price,
                exit_price=exit_price,
                exit_reason=exit_reason,
            )

    last = candles[-1]
    return _trade(
        signal,
        status="open_ended",
        entry_time=entry_candle.ts,
        exit_time=last.ts,
        entry_price=signal.entry_price,
        exit_price=last.close,
        exit_reason="horizon_close",
    )


def _entry_touched(signal: BacktestSignal, candle: HistoricalCandle) -> bool:
    if signal.direction == "long":
        return candle.low <= signal.entry_price
    return candle.high >= signal.entry_price


def _exit_touched(signal: BacktestSignal, candle: HistoricalCandle) -> str | None:
    if signal.direction == "long":
        if candle.low <= signal.stop_price:
            return "stop_loss"
        if signal.take_profit_price is not None and candle.high >= signal.take_profit_price:
            return "take_profit"
    else:
        if candle.high >= signal.stop_price:
            return "stop_loss"
        if signal.take_profit_price is not None and candle.low <= signal.take_profit_price:
            return "take_profit"
    return None


def _trade(
    signal: BacktestSignal,
    *,
    status: str,
    exit_reason: str,
    entry_time: datetime | None = None,
    exit_time: datetime | None = None,
    entry_price: float | None = None,
    exit_price: float | None = None,
) -> BacktestTrade:
    r_multiple, return_pct = _measure(signal, entry_price, exit_price)
    return BacktestTrade(
        event_id=signal.event_id,
        symbol=signal.symbol,
        strategy=signal.strategy,
        direction=signal.direction,
        status=status,
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        r_multiple=r_multiple,
        return_pct=return_pct,
    )


def _measure(
    signal: BacktestSignal,
    entry_price: float | None,
    exit_price: float | None,
) -> tuple[float | None, float | None]:
    if entry_price is None or exit_price is None:
        return None, None
    pnl_per_unit = (
        exit_price - entry_price if signal.direction == "long" else entry_price - exit_price
    )
    risk_per_unit = abs(entry_price - signal.stop_price)
    r_multiple = pnl_per_unit / risk_per_unit if risk_per_unit > 0 else None
    return_pct = pnl_per_unit / entry_price if entry_price else None
    return r_multiple, return_pct


def _parse_ts(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
