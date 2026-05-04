"""Tests for Binance exchange filters."""

from decimal import Decimal

import pytest

from execution.exchange_filters import (
    BinanceExchangeInfoClient,
    ExchangeFilterError,
    parse_symbol_filters,
)


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


# --------------------------------------------------------------------------
# BinanceExchangeInfoClient regression tests.
#
# Binance Futures /fapi/v1/exchangeInfo silently ignores the ``?symbol=``
# query parameter and returns the full listing. On mainnet (~700 symbols)
# the previous ``symbols[0]`` lookup served BTCUSDT's filter for any
# non-BTCUSDT request — the Gate 6 first canary on 2026-05-04 was aborted
# fail-closed by ``assert_min_notional`` precisely because of this bug.
# These tests pin the new behaviour.
# --------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._data


def _multi_symbol_payload() -> dict[str, object]:
    """Mirror the actual mainnet shape: many symbols, BTCUSDT first."""
    return {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "50"},
                ],
            },
            {
                "symbol": "ETHUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
            {
                "symbol": "BCHUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "minQty": "0.01", "stepSize": "0.01"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
        ]
    }


def test_binance_client_picks_requested_symbol_not_first(monkeypatch) -> None:
    """Regression: if the API returns BTCUSDT first, looking up ETHUSDT
    must return ETHUSDT's filter (tickSize 0.01, notional 5), not BTCUSDT's
    (tickSize 0.10, notional 50). This is the exact failure mode that
    aborted the Gate 6 first canary on 2026-05-04."""

    def stub_get(url, params=None, timeout=None):  # noqa: ANN001, ANN202
        return _StubResponse(_multi_symbol_payload())

    monkeypatch.setattr("execution.exchange_filters.httpx.get", stub_get)

    client = BinanceExchangeInfoClient(base_url="https://example.invalid")
    # Use the -PERP suffix to also exercise normalize_symbol.
    eth = client.symbol_filters("ETHUSDT-PERP")

    assert eth.symbol == "ETHUSDT"
    assert eth.tick_size == Decimal("0.01")
    assert eth.min_notional == Decimal("5")
    # And BTCUSDT still resolves correctly.
    btc = client.symbol_filters("BTCUSDT-PERP")
    assert btc.symbol == "BTCUSDT"
    assert btc.tick_size == Decimal("0.10")
    assert btc.min_notional == Decimal("50")


def test_binance_client_raises_on_unknown_symbol(monkeypatch) -> None:
    """If the requested symbol is not present in the API response, fail
    closed instead of silently returning some other symbol's filter."""

    def stub_get(url, params=None, timeout=None):  # noqa: ANN001, ANN202
        return _StubResponse(_multi_symbol_payload())

    monkeypatch.setattr("execution.exchange_filters.httpx.get", stub_get)

    client = BinanceExchangeInfoClient(base_url="https://example.invalid")
    with pytest.raises(ExchangeFilterError, match="binance_exchange_info_missing_symbol"):
        client.symbol_filters("DOGEUSDT-PERP")
