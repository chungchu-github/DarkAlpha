from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock

from dark_alpha_phase_one.calculations import Candle


@dataclass(frozen=True)
class FundingRatePoint:
    funding_rate: float
    funding_time: int


@dataclass(frozen=True)
class SymbolSnapshot:
    symbol: str
    price: float | None
    klines_1m: list[Candle]
    last_price_ts: datetime | None
    last_kline_close_ts: datetime | None
    data_source_mode: str
    last_funding_rate: float | None
    next_funding_time_ms: int | None
    mark_price: float | None
    funding_rate_history: list[FundingRatePoint]
    open_interest: float | None
    open_interest_ts: datetime | None
    funding_ts: datetime | None
    open_interest_series: list[tuple[datetime, float]]


class DataStore:
    def __init__(self, symbols: list[str], max_price_points: int = 600, max_klines: int = 1440) -> None:
        self._lock = RLock()
        self._mode = "rest"
        self._prices: dict[str, deque[tuple[datetime, float]]] = {
            symbol: deque(maxlen=max_price_points) for symbol in symbols
        }
        self._klines: dict[str, deque[Candle]] = {symbol: deque(maxlen=max_klines) for symbol in symbols}
        self._last_price_ts: dict[str, datetime | None] = {symbol: None for symbol in symbols}
        self._last_kline_close_ts: dict[str, datetime | None] = {symbol: None for symbol in symbols}
        self._last_ws_kline_open_time: dict[str, int | None] = {symbol: None for symbol in symbols}

        self._last_funding_rate: dict[str, float | None] = {symbol: None for symbol in symbols}
        self._next_funding_time_ms: dict[str, int | None] = {symbol: None for symbol in symbols}
        self._mark_price: dict[str, float | None] = {symbol: None for symbol in symbols}
        self._funding_rate_history: dict[str, list[FundingRatePoint]] = {symbol: [] for symbol in symbols}
        self._open_interest: dict[str, float | None] = {symbol: None for symbol in symbols}
        self._open_interest_ts: dict[str, datetime | None] = {symbol: None for symbol in symbols}
        self._funding_ts: dict[str, datetime | None] = {symbol: None for symbol in symbols}
        self._open_interest_series: dict[str, deque[tuple[datetime, float]]] = {
            symbol: deque(maxlen=24 * 60 * 6) for symbol in symbols
        }

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self._mode = mode

    def mode(self) -> str:
        with self._lock:
            return self._mode

    def update_price(self, symbol: str, price: float, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(tz=timezone.utc)
        with self._lock:
            self._prices[symbol].append((ts, price))
            self._last_price_ts[symbol] = ts

    def merge_klines(self, symbol: str, klines: list[Candle], ts: datetime | None = None) -> None:
        ts = ts or datetime.now(tz=timezone.utc)
        with self._lock:
            if not klines:
                return
            self._klines[symbol].clear()
            self._klines[symbol].extend(klines)
            self._last_kline_close_ts[symbol] = ts
            self._last_ws_kline_open_time[symbol] = None

    def upsert_ws_kline(
        self,
        symbol: str,
        candle: Candle,
        open_time_ms: int,
        is_closed: bool,
        ts: datetime | None = None,
    ) -> None:
        ts = ts or datetime.now(tz=timezone.utc)
        with self._lock:
            last_open = self._last_ws_kline_open_time[symbol]
            if self._klines[symbol] and last_open == open_time_ms:
                self._klines[symbol][-1] = candle
            else:
                self._klines[symbol].append(candle)
                self._last_ws_kline_open_time[symbol] = open_time_ms

            if is_closed:
                self._last_kline_close_ts[symbol] = ts

    def update_premium_index(
        self,
        symbol: str,
        *,
        mark_price: float,
        last_funding_rate: float,
        next_funding_time_ms: int,
        ts: datetime | None = None,
    ) -> None:
        ts = ts or datetime.now(tz=timezone.utc)
        with self._lock:
            self._mark_price[symbol] = mark_price
            self._last_funding_rate[symbol] = last_funding_rate
            self._next_funding_time_ms[symbol] = next_funding_time_ms
            self._funding_ts[symbol] = ts

    def update_funding_rate_history(
        self,
        symbol: str,
        history: list[FundingRatePoint],
        ts: datetime | None = None,
    ) -> None:
        ts = ts or datetime.now(tz=timezone.utc)
        with self._lock:
            self._funding_rate_history[symbol] = history
            self._funding_ts[symbol] = ts

    def update_open_interest(self, symbol: str, open_interest: float, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(tz=timezone.utc)
        with self._lock:
            self._open_interest[symbol] = open_interest
            self._open_interest_ts[symbol] = ts
            self._open_interest_series[symbol].append((ts, open_interest))

    def snapshot(self, symbol: str) -> SymbolSnapshot:
        with self._lock:
            latest_price = self._prices[symbol][-1][1] if self._prices[symbol] else None
            return SymbolSnapshot(
                symbol=symbol,
                price=latest_price,
                klines_1m=list(self._klines[symbol]),
                last_price_ts=self._last_price_ts[symbol],
                last_kline_close_ts=self._last_kline_close_ts[symbol],
                data_source_mode=self._mode,
                last_funding_rate=self._last_funding_rate[symbol],
                next_funding_time_ms=self._next_funding_time_ms[symbol],
                mark_price=self._mark_price[symbol],
                funding_rate_history=list(self._funding_rate_history[symbol]),
                open_interest=self._open_interest[symbol],
                open_interest_ts=self._open_interest_ts[symbol],
                funding_ts=self._funding_ts[symbol],
                open_interest_series=list(self._open_interest_series[symbol]),
            )

    def buffer_sizes(self, symbol: str) -> tuple[int, int]:
        with self._lock:
            return len(self._prices[symbol]), len(self._klines[symbol])
