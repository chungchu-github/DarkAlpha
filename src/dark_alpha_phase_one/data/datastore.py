from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock

from dark_alpha_phase_one.calculations import Candle


@dataclass(frozen=True)
class SymbolSnapshot:
    symbol: str
    price: float | None
    klines_1m: list[Candle]
    last_price_ts: datetime | None
    last_kline_close_ts: datetime | None
    data_source_mode: str


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

    def upsert_ws_kline(self, symbol: str, candle: Candle, open_time_ms: int, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(tz=timezone.utc)
        with self._lock:
            last_open = self._last_ws_kline_open_time[symbol]
            if self._klines[symbol] and last_open == open_time_ms:
                self._klines[symbol][-1] = candle
            else:
                self._klines[symbol].append(candle)
                self._last_ws_kline_open_time[symbol] = open_time_ms
            self._last_kline_close_ts[symbol] = ts

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
            )

    def buffer_sizes(self, symbol: str) -> tuple[int, int]:
        with self._lock:
            return len(self._prices[symbol]), len(self._klines[symbol])
