from __future__ import annotations

from datetime import datetime, timezone
import logging

from dark_alpha_phase_one.binance_client import BinanceFuturesClient
from dark_alpha_phase_one.calculations import Candle


class BinanceRestDataClient:
    def __init__(self) -> None:
        self._client = BinanceFuturesClient()

    def fetch_price(self, symbol: str) -> tuple[float, datetime]:
        price = self._client.get_latest_price(symbol)
        ts = datetime.now(tz=timezone.utc)
        return price, ts

    def fetch_klines(self, symbol: str, limit: int) -> tuple[list[Candle], datetime]:
        klines = self._client.get_1m_klines(symbol, limit=limit)
        ts = datetime.now(tz=timezone.utc)
        logging.debug("REST fetched %s klines for %s", len(klines), symbol)
        return klines, ts
