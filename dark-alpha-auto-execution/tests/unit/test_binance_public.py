"""Unit tests for market_data.binance_public — all HTTP mocked."""

from typing import Any

import httpx
import pytest

from market_data.binance_public import BinancePublicClient


def test_normalize_strips_perp_suffix() -> None:
    assert BinancePublicClient._normalize("BTCUSDT-PERP") == "BTCUSDT"
    assert BinancePublicClient._normalize("ETHUSDT_PERP") == "ETHUSDT"
    assert BinancePublicClient._normalize("SOLUSDT") == "SOLUSDT"


def test_last_price_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"symbol": "BTCUSDT", "price": "94250.5"}

    def fake_get(url: str, params: dict[str, str], timeout: float) -> FakeResp:
        assert params == {"symbol": "BTCUSDT"}
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get)
    client = BinancePublicClient()
    assert client.last_price("BTCUSDT-PERP") == pytest.approx(94250.5)


def test_last_price_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*_args: Any, **_kw: Any) -> None:
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    client = BinancePublicClient()
    assert client.last_price("BTCUSDT-PERP") is None
