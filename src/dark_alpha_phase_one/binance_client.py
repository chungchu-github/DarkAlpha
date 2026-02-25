from __future__ import annotations

import logging

import requests

from .calculations import Candle


class BinanceFuturesClient:
    def __init__(self, base_url: str = "https://fapi.binance.com") -> None:
        self.base_url = base_url
        self.session = requests.Session()

    def get_latest_price(self, symbol: str) -> float:
        resp = self.session.get(
            f"{self.base_url}/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=10
        )
        resp.raise_for_status()
        price = float(resp.json()["price"])
        logging.debug("Fetched latest price for %s: %.4f", symbol, price)
        return price

    def get_1m_klines(self, symbol: str, limit: int = 300) -> list[Candle]:
        resp = self.session.get(
            f"{self.base_url}/fapi/v1/klines",
            params={"symbol": symbol, "interval": "1m", "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
        candles = [
            Candle(
                open=float(kline[1]),
                high=float(kline[2]),
                low=float(kline[3]),
                close=float(kline[4]),
            )
            for kline in raw
        ]
        logging.debug("Fetched %d 1m klines for %s", len(candles), symbol)
        return candles
