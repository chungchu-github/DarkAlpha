"""Tests for Gate 2 test payload builder."""

from decimal import Decimal

import httpx

from execution.exchange_filters import StaticExchangeFilterProvider, SymbolFilters
from execution.gate2_test import Gate2TestBuilder


def _filters() -> StaticExchangeFilterProvider:
    return StaticExchangeFilterProvider(
        SymbolFilters(
            symbol="ETHUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
        )
    )


def test_gate2_builder_generates_filtered_long_payload(monkeypatch) -> None:
    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"price": "2329.33"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: FakeResp())

    payload = Gate2TestBuilder(filters=_filters()).build_bracket_payload(
        symbol="ETHUSDT-PERP",
        side="LONG",
        trace_id="trace-1",
    )

    assert payload.mark_price == 2329.33
    assert payload.payload["symbol"] == "ETHUSDT"
    assert payload.payload["side"] == "LONG"
    assert payload.payload["entry"] == 2306.03
    assert payload.payload["stop"] == 1281.13
    assert payload.payload["trace_id"] == "trace-1"
