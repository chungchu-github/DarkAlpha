from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dark_alpha_phase_one.calculations import Candle


@dataclass(frozen=True)
class SignalContext:
    symbol: str
    timestamp: datetime
    price: float
    klines_1m: list[Candle]
    return_5m: float
    atr_15m: float
    atr_15m_baseline: float
    funding_rate: float
    open_interest: float
    mark_price: float
    open_interest_zscore_15m: float | None
    open_interest_delta_15m: float | None
    last_kline_close_ts: datetime | None
