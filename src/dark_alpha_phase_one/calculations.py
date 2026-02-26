from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float


def calculate_return(closes: list[float], lookback_minutes: int = 5) -> float:
    required = lookback_minutes + 1
    if len(closes) < required:
        raise ValueError(f"Need at least {required} closes to compute {lookback_minutes}m return")

    current = closes[-1]
    previous = closes[-(lookback_minutes + 1)]
    if previous == 0:
        raise ValueError("Previous close cannot be zero")
    return (current - previous) / previous


def aggregate_klines_to_window(candles_1m: list[Candle], window: int = 15) -> list[Candle]:
    if len(candles_1m) < window:
        return []

    grouped: list[Candle] = []
    for i in range(window, len(candles_1m) + 1, window):
        chunk = candles_1m[i - window : i]
        grouped.append(
            Candle(
                open=chunk[0].open,
                high=max(c.high for c in chunk),
                low=min(c.low for c in chunk),
                close=chunk[-1].close,
            )
        )
    return grouped


def true_ranges(candles: list[Candle]) -> list[float]:
    trs: list[float] = []
    for idx, candle in enumerate(candles):
        if idx == 0:
            tr = candle.high - candle.low
        else:
            prev_close = candles[idx - 1].close
            tr = max(
                candle.high - candle.low,
                abs(candle.high - prev_close),
                abs(candle.low - prev_close),
            )
        trs.append(tr)
    return trs


def atr_series(candles: list[Candle], period: int = 14) -> list[float]:
    trs = true_ranges(candles)
    if len(trs) < period:
        return []

    atrs: list[float] = []
    for end in range(period, len(trs) + 1):
        window = trs[end - period : end]
        atrs.append(sum(window) / period)
    return atrs


def calculate_position_usdt(entry: float, stop: float, max_risk_usdt: float) -> float:
    risk_ratio = abs(entry - stop) / entry
    if risk_ratio <= 0:
        raise ValueError("Risk ratio must be positive")
    return max_risk_usdt / risk_ratio
