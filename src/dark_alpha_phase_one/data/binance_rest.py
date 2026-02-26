from __future__ import annotations

from datetime import datetime, timezone
import logging

from dark_alpha_phase_one.binance_client import BinanceFuturesClient
from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.data.datastore import FundingRatePoint


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

    def fetch_premium_index(self, symbol: str) -> tuple[float, float, int, datetime]:
        payload = self._client.get_premium_index(symbol)
        ts = datetime.now(tz=timezone.utc)
        return (
            float(payload["markPrice"]),
            float(payload["lastFundingRate"]),
            int(payload["nextFundingTime"]),
            ts,
        )

    def fetch_funding_rate_history(self, symbol: str, limit: int = 3) -> tuple[list[FundingRatePoint], datetime]:
        payload = self._client.get_funding_rate_history(symbol, limit=limit)
        ts = datetime.now(tz=timezone.utc)
        history = [
            FundingRatePoint(
                funding_rate=float(item["fundingRate"]),
                funding_time=int(item["fundingTime"]),
            )
            for item in payload
        ]
        return history, ts

    def fetch_open_interest(self, symbol: str) -> tuple[float, datetime]:
        payload = self._client.get_open_interest(symbol)
        ts = datetime.now(tz=timezone.utc)
        return float(payload["openInterest"]), ts
