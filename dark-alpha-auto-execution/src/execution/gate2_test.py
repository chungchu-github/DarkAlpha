"""Gate 2 testnet helper utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .binance_testnet_broker import normalize_symbol
from .exchange_filters import ExchangeFilterProvider

_TESTNET_BASE_URL = "https://testnet.binancefuture.com"


@dataclass(frozen=True)
class Gate2BracketPayload:
    payload: dict[str, object]
    mark_price: float


class Gate2TestBuilder:
    def __init__(
        self,
        *,
        filters: ExchangeFilterProvider,
        base_url: str = _TESTNET_BASE_URL,
        timeout: float = 10.0,
    ) -> None:
        self._filters = filters
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def build_bracket_payload(
        self,
        *,
        symbol: str,
        side: str,
        trace_id: str,
        strategy: str = "gate2_test_signal",
        entry_offset_pct: float = 0.01,
        stop_distance_pct: float = 0.45,
        take_profit_distance_pct: float = 0.85,
        max_risk_usdt: float = 50.0,
        confidence: float = 90.0,
    ) -> Gate2BracketPayload:
        mark = self._last_price(symbol)
        filters = self._filters.symbol_filters(symbol)
        upper_side = side.upper()
        if upper_side == "LONG":
            entry = mark * (1 - entry_offset_pct)
            stop = mark * (1 - stop_distance_pct)
            take_profit = mark * (1 + take_profit_distance_pct)
        elif upper_side == "SHORT":
            entry = mark * (1 + entry_offset_pct)
            stop = mark * (1 + stop_distance_pct)
            take_profit = mark * (1 - take_profit_distance_pct)
        else:
            raise ValueError("side must be LONG or SHORT")

        entry_price = float(filters.price(entry))
        stop_price = float(filters.price(stop))
        take_profit_price = float(filters.price(take_profit))
        position_usdt = max(entry_price * float(filters.quantity(0.05)), 10.0)

        return Gate2BracketPayload(
            mark_price=mark,
            payload={
                "symbol": normalize_symbol(symbol),
                "strategy": strategy,
                "side": upper_side,
                "entry": entry_price,
                "stop": stop_price,
                "leverage_suggest": 1,
                "position_usdt": position_usdt,
                "max_risk_usdt": max_risk_usdt,
                "ttl_minutes": 15,
                "rationale": "Gate2 generated testnet bracket signal",
                "created_at": datetime.now(tz=UTC).isoformat(),
                "priority": 1,
                "confidence": confidence,
                "take_profit": take_profit_price,
                "invalid_condition": "manual gate2 test invalid if stop is touched",
                "risk_level": "low",
                "oi_status": "fresh",
                "data_health": {"status": "fresh", "reason": "gate2_test"},
                "trace_id": trace_id,
            },
        )

    def _last_price(self, symbol: str) -> float:
        resp = httpx.get(
            f"{self._base_url}/fapi/v1/ticker/price",
            params={"symbol": normalize_symbol(symbol)},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
