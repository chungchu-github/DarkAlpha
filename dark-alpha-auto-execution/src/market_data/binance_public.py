"""Public Binance USDT-M futures klines — read-only, no API key required.

Used only in shadow mode to mark open positions to market. Never submits orders.
Endpoints: https://fapi.binance.com/fapi/v1/klines
"""

from typing import Protocol

import httpx
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://fapi.binance.com"
_TIMEOUT = 5.0


class PriceSource(Protocol):
    def last_price(self, symbol: str) -> float | None: ...


class BinancePublicClient:
    """Minimal read-only client. Returns None on any failure — never raises."""

    def __init__(self, base_url: str = _BASE_URL, timeout: float = _TIMEOUT) -> None:
        self._base = base_url
        self._timeout = timeout

    def last_price(self, symbol: str) -> float | None:
        """Fetch latest trade price for a PERP symbol (e.g. 'BTCUSDT-PERP')."""
        api_symbol = self._normalize(symbol)
        try:
            resp = httpx.get(
                f"{self._base}/fapi/v1/ticker/price",
                params={"symbol": api_symbol},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            price = float(resp.json()["price"])
            return price
        except Exception as exc:  # noqa: BLE001
            log.warning("binance.last_price_failed", symbol=symbol, error=str(exc))
            return None

    @staticmethod
    def _normalize(symbol: str) -> str:
        """Strip -PERP / _PERP — Binance uses 'BTCUSDT' for USDT-M perps."""
        for suffix in ("-PERP", "_PERP"):
            if symbol.endswith(suffix):
                return symbol[: -len(suffix)]
        return symbol
