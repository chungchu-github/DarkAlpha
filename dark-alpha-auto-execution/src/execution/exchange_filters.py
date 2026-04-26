"""Binance Futures symbol filters.

Gate 2 uses these filters to normalize prices and quantities before submitting
orders. That keeps testnet exercises close to the real exchange contract rules
instead of relying on ad hoc float formatting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any, Protocol

import httpx

_TESTNET_BASE_URL = "https://testnet.binancefuture.com"
_DEFAULT_TIMEOUT = 10.0


class ExchangeFilterError(RuntimeError):
    """Raised when exchange filters cannot be loaded or applied."""


class ExchangeFilterProvider(Protocol):
    def symbol_filters(self, symbol: str) -> SymbolFilters: ...


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal

    def price(self, value: float) -> str:
        rounded = _round_down(Decimal(str(value)), self.tick_size)
        if rounded <= 0:
            raise ExchangeFilterError(f"invalid_filtered_price:{self.symbol}")
        return _fmt_decimal(rounded)

    def quantity(self, value: float) -> str:
        rounded = _round_down(Decimal(str(value)), self.step_size)
        if rounded < self.min_qty:
            raise ExchangeFilterError(
                f"quantity_below_min_qty:{self.symbol}:{rounded}<{self.min_qty}"
            )
        return _fmt_decimal(rounded)

    def assert_min_notional(self, *, price: float, quantity: float) -> None:
        notional = Decimal(str(price)) * Decimal(str(quantity))
        if notional < self.min_notional:
            raise ExchangeFilterError(
                f"notional_below_min_notional:{self.symbol}:{_fmt_decimal(notional)}<{self.min_notional}"
            )


class BinanceExchangeInfoClient:
    def __init__(
        self,
        *,
        base_url: str = _TESTNET_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache: dict[str, SymbolFilters] = {}

    def symbol_filters(self, symbol: str) -> SymbolFilters:
        api_symbol = normalize_symbol(symbol)
        if api_symbol not in self._cache:
            self._cache[api_symbol] = self._fetch_symbol_filters(api_symbol)
        return self._cache[api_symbol]

    def _fetch_symbol_filters(self, symbol: str) -> SymbolFilters:
        try:
            resp = httpx.get(
                f"{self._base_url}/fapi/v1/exchangeInfo",
                params={"symbol": symbol},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise ExchangeFilterError(f"binance_exchange_info_failed:{exc}") from exc

        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not symbols:
            raise ExchangeFilterError(f"binance_exchange_info_missing_symbol:{symbol}")
        return parse_symbol_filters(symbols[0])


class StaticExchangeFilterProvider:
    def __init__(self, filters: SymbolFilters) -> None:
        self._filters = filters

    def symbol_filters(self, symbol: str) -> SymbolFilters:
        api_symbol = normalize_symbol(symbol)
        if api_symbol != self._filters.symbol:
            raise ExchangeFilterError(f"missing_static_filters:{api_symbol}")
        return self._filters


def normalize_symbol(symbol: str) -> str:
    for suffix in ("-PERP", "_PERP"):
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def parse_symbol_filters(payload: dict[str, Any]) -> SymbolFilters:
    symbol = str(payload["symbol"])
    by_type = {str(item.get("filterType")): item for item in payload.get("filters", [])}

    price_filter = by_type.get("PRICE_FILTER") or {}
    lot_filter = by_type.get("LOT_SIZE") or {}
    market_lot_filter = by_type.get("MARKET_LOT_SIZE") or {}
    min_notional_filter = by_type.get("MIN_NOTIONAL") or {}

    step_size = Decimal(
        str(lot_filter.get("stepSize") or market_lot_filter.get("stepSize") or "0.001")
    )
    min_qty = Decimal(str(lot_filter.get("minQty") or market_lot_filter.get("minQty") or step_size))
    min_notional = Decimal(
        str(min_notional_filter.get("notional") or min_notional_filter.get("minNotional") or "0")
    )

    return SymbolFilters(
        symbol=symbol,
        tick_size=Decimal(str(price_filter.get("tickSize") or "0.01")),
        step_size=step_size,
        min_qty=min_qty,
        min_notional=min_notional,
    )


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _fmt_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
