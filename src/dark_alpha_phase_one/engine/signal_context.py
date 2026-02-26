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
