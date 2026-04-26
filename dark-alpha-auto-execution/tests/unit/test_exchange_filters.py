"""Tests for Binance exchange filters."""

from decimal import Decimal

import pytest

from execution.exchange_filters import ExchangeFilterError, parse_symbol_filters


def _payload() -> dict[str, object]:
    return {
        "symbol": "ETHUSDT",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }


def test_parse_symbol_filters_rounds_down_price_and_quantity() -> None:
    filters = parse_symbol_filters(_payload())

    assert filters.tick_size == Decimal("0.01")
    assert filters.price(2300.129) == "2300.12"
    assert filters.quantity(0.0509) == "0.05"


def test_symbol_filters_reject_below_min_qty() -> None:
    filters = parse_symbol_filters(_payload())

    with pytest.raises(ExchangeFilterError, match="quantity_below_min_qty"):
        filters.quantity(0.0009)


def test_symbol_filters_reject_below_min_notional() -> None:
    filters = parse_symbol_filters(_payload())

    with pytest.raises(ExchangeFilterError, match="notional_below_min_notional"):
        filters.assert_min_notional(price=100.0, quantity=0.01)
