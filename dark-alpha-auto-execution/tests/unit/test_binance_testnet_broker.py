"""Tests for Binance testnet broker payload and safety behavior."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import httpx
import pytest

from execution.binance_testnet_broker import (
    BinanceSignedClient,
    BinanceTestnetBroker,
    LiveBrokerError,
    format_price,
    normalize_symbol,
    sanitize_binance_error_text,
    sanitize_binance_url,
)
from execution.exchange_filters import StaticExchangeFilterProvider, SymbolFilters
from execution.live_safety import LiveExecutionConfig
from strategy.schemas import ExecutionTicket, PlannedOrder


class FakeClient:
    def __init__(self) -> None:
        self.leverage_calls: list[tuple[str, int]] = []
        self.new_order_calls: list[Mapping[str, Any]] = []
        self.new_algo_order_calls: list[Mapping[str, Any]] = []
        self.cancelled: list[str] = []
        self.algo_cancelled: list[str] = []
        self.positions: list[Mapping[str, Any]] = [{"positionAmt": "0"}]
        self.orders: list[Mapping[str, Any]] = []
        self.algo_orders: list[Mapping[str, Any]] = []
        self.fail_on_order: int | None = None

    def set_leverage(self, symbol: str, leverage: int) -> Mapping[str, Any]:
        self.leverage_calls.append((symbol, leverage))
        return {"symbol": normalize_symbol(symbol), "leverage": leverage}

    def position_risk(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.positions

    def open_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.orders

    def open_algo_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        return self.algo_orders

    def new_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self.new_order_calls.append(params)
        if self.fail_on_order is not None and len(self.new_order_calls) == self.fail_on_order:
            raise LiveBrokerError("rejected")
        return {
            "clientOrderId": params["newClientOrderId"],
            "orderId": f"ex-{len(self.new_order_calls)}",
            "symbol": params["symbol"],
            "side": params["side"],
            "type": params["type"],
            "status": "NEW",
            "price": params.get("price", "0"),
            "origQty": params["quantity"],
        }

    def new_algo_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self.new_algo_order_calls.append(params)
        if (
            self.fail_on_order is not None
            and (len(self.new_order_calls) + len(self.new_algo_order_calls)) == self.fail_on_order
        ):
            raise LiveBrokerError("rejected")
        return {
            "clientAlgoId": params["clientAlgoId"],
            "algoId": f"algo-{len(self.new_algo_order_calls)}",
            "symbol": params["symbol"],
            "side": params["side"],
            "type": params["type"],
            "algoStatus": "NEW",
            "triggerPrice": params["triggerPrice"],
            "quantity": params["quantity"],
        }

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:
        return {
            "clientOrderId": client_order_id,
            "symbol": normalize_symbol(symbol),
            "status": "NEW",
            "executedQty": "0",
            "origQty": "0.01",
        }

    def query_algo_order(self, symbol: str, client_algo_id: str) -> Mapping[str, Any]:
        return {
            "clientAlgoId": client_algo_id,
            "symbol": normalize_symbol(symbol),
            "algoStatus": "NEW",
            "executedQty": "0",
            "quantity": "0.01",
        }

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:
        self.cancelled.append(symbol)
        return {"code": 200, "msg": "success"}

    def cancel_all_open_algo_orders(self, symbol: str) -> Mapping[str, Any]:
        self.algo_cancelled.append(symbol)
        return {"code": 200, "msg": "success"}


def _config(environment: str = "testnet") -> LiveExecutionConfig:
    return LiveExecutionConfig(
        mode="live",
        environment=environment,
        allow_mainnet=False,
        require_gate_authorization=False,
        gate_authorization_file="missing",
    )


def _filters(symbol: str = "BTCUSDT") -> StaticExchangeFilterProvider:
    return StaticExchangeFilterProvider(
        SymbolFilters(
            symbol=symbol,
            tick_size=Decimal("0.1"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("1"),
        )
    )


def _ticket() -> ExecutionTicket:
    return ExecutionTicket(
        ticket_id="01PHASE5BROKERTEST",
        source_event_id="evt-1",
        symbol="BTCUSDT-PERP",
        direction="long",
        regime="vol_breakout_card",
        ranking_score=8.0,
        shadow_mode=False,
        gate="gate2",
        entry_price=100.0,
        stop_price=99.0,
        take_profit_price=102.0,
        quantity=0.01,
        notional_usd=1.0,
        leverage=2.0,
        risk_usd=0.01,
        orders=[
            PlannedOrder(
                role="entry",
                side="buy",
                type="limit",
                symbol="BTCUSDT-PERP",
                price=100.0,
                quantity=0.01,
            ),
            PlannedOrder(
                role="stop",
                side="sell",
                type="stop_market",
                symbol="BTCUSDT-PERP",
                price=99.0,
                quantity=0.01,
                reduce_only=True,
            ),
            PlannedOrder(
                role="take_profit",
                side="sell",
                type="limit",
                symbol="BTCUSDT-PERP",
                price=102.0,
                quantity=0.01,
                reduce_only=True,
            ),
        ],
        created_at="2026-04-18T00:00:00+00:00",
    )


def test_formats_binance_symbols_and_prices() -> None:
    assert normalize_symbol("BTCUSDT-PERP") == "BTCUSDT"
    assert format_price(100.12000000) == "100.12"


def test_submit_ticket_builds_entry_stop_take_profit_payloads() -> None:
    client = FakeClient()
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    acks = broker.submit_ticket(_ticket())

    assert len(acks) == 3
    assert client.leverage_calls == [("BTCUSDT-PERP", 2)]
    entry = client.new_order_calls[0]
    stop, take_profit = client.new_algo_order_calls
    assert entry["type"] == "LIMIT"
    assert entry["timeInForce"] == "GTC"
    assert entry["newClientOrderId"].startswith("DAENB")
    assert stop["type"] == "STOP_MARKET"
    assert stop["algoType"] == "CONDITIONAL"
    assert stop["clientAlgoId"].startswith("DASTS")
    assert stop["triggerPrice"] == "99"
    assert stop["reduceOnly"] == "true"
    assert take_profit["type"] == "TAKE_PROFIT_MARKET"
    assert take_profit["clientAlgoId"].startswith("DATPS")
    assert take_profit["reduceOnly"] == "true"


def test_submit_ticket_applies_exchange_filters() -> None:
    client = FakeClient()
    ticket = _ticket().model_copy(update={"entry_price": 100.19})
    ticket.orders[0].price = 100.19
    ticket.orders[0].quantity = 0.0109
    ticket.orders[1].quantity = 0.0109
    ticket.orders[2].quantity = 0.0109
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    broker.submit_ticket(ticket)

    entry = client.new_order_calls[0]
    assert entry["price"] == "100.1"
    assert entry["quantity"] == "0.01"


def test_submit_ticket_rounds_leverage_up_to_minimum_one() -> None:
    client = FakeClient()
    ticket = _ticket().model_copy(update={"leverage": 0.2})
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    broker.submit_ticket(ticket)

    assert client.leverage_calls == [("BTCUSDT-PERP", 1)]


def test_submit_ticket_blocks_existing_position() -> None:
    client = FakeClient()
    client.positions = [{"positionAmt": "0.01"}]
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    with pytest.raises(LiveBrokerError, match="existing_exchange_position"):
        broker.submit_ticket(_ticket())


def test_submit_ticket_blocks_open_orders() -> None:
    client = FakeClient()
    client.orders = [{"orderId": 1}]
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    with pytest.raises(LiveBrokerError, match="existing_exchange_open_orders"):
        broker.submit_ticket(_ticket())


def test_submit_ticket_blocks_open_algo_orders() -> None:
    client = FakeClient()
    client.algo_orders = [{"algoId": 1}]
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    with pytest.raises(LiveBrokerError, match="existing_exchange_open_algo_orders"):
        broker.submit_ticket(_ticket())


def test_submit_ticket_cancels_all_when_bracket_submission_fails() -> None:
    client = FakeClient()
    client.fail_on_order = 2
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    with pytest.raises(LiveBrokerError, match="rejected"):
        broker.submit_ticket(_ticket())

    assert client.cancelled == ["BTCUSDT-PERP"]
    assert client.algo_cancelled == ["BTCUSDT-PERP"]


class PartialThenCancelFailClient(FakeClient):
    """Bracket submit fails on order #2 AND the cancel sweep itself errors."""

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:  # type: ignore[override]
        super().cancel_all_open_orders(symbol)
        raise LiveBrokerError("cancel_failed")


def test_submit_ticket_partial_failure_cancel_unknown_does_not_mask_original_error() -> None:
    """Task 8 — when both submit AND the cancel sweep fail, the broker must
    still surface the *original* submit failure (not swallow it under the
    cancel error)."""
    client = PartialThenCancelFailClient()
    client.fail_on_order = 2  # entry succeeds, stop fails
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    with pytest.raises(LiveBrokerError) as exc_info:
        broker.submit_ticket(_ticket())

    # The original submit error wins; cancel failure is logged separately.
    assert "rejected" in str(exc_info.value)
    # Cancel was attempted at least once.
    assert client.cancelled == ["BTCUSDT-PERP"]


def test_testnet_broker_refuses_mainnet_config() -> None:
    broker = BinanceTestnetBroker(
        client=FakeClient(), config=_config("mainnet"), filters=_filters()
    )

    with pytest.raises(LiveBrokerError, match="refuses_non_testnet"):
        broker.submit_ticket(_ticket())


def test_emergency_close_symbol_submits_reduce_only_market() -> None:
    client = FakeClient()
    client.positions = [{"positionAmt": "0.0309"}]
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    ack = broker.emergency_close_symbol("BTCUSDT-PERP")

    assert ack is not None
    payload = client.new_order_calls[0]
    assert payload["type"] == "MARKET"
    assert payload["side"] == "SELL"
    assert payload["reduceOnly"] == "true"
    assert payload["quantity"] == "0.03"


def test_emergency_close_symbol_returns_none_when_flat() -> None:
    client = FakeClient()
    broker = BinanceTestnetBroker(client=client, config=_config(), filters=_filters())

    assert broker.emergency_close_symbol("BTCUSDT-PERP") is None
    assert not client.new_order_calls


# ---------------------------------------------------------------------------
# Task 4 — server time sync
# ---------------------------------------------------------------------------


def _make_signed_client(**overrides: Any) -> BinanceSignedClient:
    kwargs: dict[str, Any] = {
        "api_key": "test-key",
        "api_secret": "test-secret",
        "base_url": "https://testnet.example.com",
        "environment": "testnet",
    }
    kwargs.update(overrides)
    return BinanceSignedClient(**kwargs)


def test_signed_request_uses_server_adjusted_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signed requests must use local_now_ms + server_time_offset_ms."""
    # Allow 60s drift for this test — we want to observe a 3s offset.
    client = _make_signed_client(max_clock_drift_ms=60_000)
    # Server is 3 seconds ahead of local clock (within drift cap).
    monkeypatch.setattr(client, "_fetch_server_time_ms", lambda: int(time_now_ms() + 3_000))

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", fake_request)

    client._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})

    sent_ts = int(captured["params"]["timestamp"])
    local_ts = time_now_ms()
    # Adjusted timestamp must be ahead of local clock by ~3 seconds.
    assert sent_ts > local_ts + 1_500
    assert sent_ts < local_ts + 4_500


def test_signed_request_blocks_when_server_time_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the server time fetch fails, no signed request goes out."""
    client = _make_signed_client()

    def _boom() -> int:
        raise LiveBrokerError("server_time_unavailable type=ConnectError")

    monkeypatch.setattr(client, "_fetch_server_time_ms", _boom)

    request_calls: list[Any] = []
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *a, **kw: request_calls.append((a, kw)) or None,  # type: ignore[func-returns-value]
    )

    with pytest.raises(LiveBrokerError, match="server_time_unavailable"):
        client._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})

    # Critical invariant: nothing went over the wire.
    assert request_calls == []


def test_signed_request_refreshes_offset_when_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired offset triggers a re-fetch on the next signed call."""
    client = _make_signed_client(server_time_ttl_seconds=0.0)
    fetch_count = {"n": 0}

    def _fetch() -> int:
        fetch_count["n"] += 1
        return time_now_ms() + 1_000  # 1s ahead

    monkeypatch.setattr(client, "_fetch_server_time_ms", _fetch)
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *a, **kw: httpx.Response(200, json={}, request=httpx.Request(a[0], a[1])),
    )

    client._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})
    client._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})

    # TTL=0 forces refresh on every call.
    assert fetch_count["n"] == 2


def test_signed_request_reuses_offset_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh offset must be reused — avoid a /fapi/v1/time hit on every call."""
    client = _make_signed_client(server_time_ttl_seconds=60.0)
    fetch_count = {"n": 0}

    def _fetch() -> int:
        fetch_count["n"] += 1
        return time_now_ms() + 1_000

    monkeypatch.setattr(client, "_fetch_server_time_ms", _fetch)
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *a, **kw: httpx.Response(200, json={}, request=httpx.Request(a[0], a[1])),
    )

    client._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})
    client._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})
    client._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})

    assert fetch_count["n"] == 1


def test_signed_request_blocks_when_clock_drift_exceeds_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If measured drift > max_clock_drift_ms, the request must fail closed."""
    client = _make_signed_client(max_clock_drift_ms=1_000)
    monkeypatch.setattr(
        client, "_fetch_server_time_ms", lambda: time_now_ms() + 60_000
    )  # 60s drift

    request_calls: list[Any] = []
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *a, **kw: request_calls.append((a, kw)) or None,  # type: ignore[func-returns-value]
    )

    with pytest.raises(LiveBrokerError, match="clock_drift_too_large"):
        client._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})

    assert request_calls == []


def test_recv_window_remains_configurable() -> None:
    client = _make_signed_client(recv_window=7_500)
    assert client._recv_window == 7_500


# ---------------------------------------------------------------------------
# Task 5 — secret redaction
# ---------------------------------------------------------------------------


def test_sanitize_binance_url_strips_query_string() -> None:
    url = (
        "https://fapi.binance.com/fapi/v1/order"
        "?symbol=BTCUSDT&timestamp=1700000000000&signature=abcdef0123456789"
    )
    assert sanitize_binance_url(url) == "/fapi/v1/order"


def test_sanitize_binance_url_handles_empty() -> None:
    assert sanitize_binance_url("") == ""


def test_sanitize_binance_error_text_redacts_signature() -> None:
    text = "request rejected: signature=abcdef1234567890"
    out = sanitize_binance_error_text(text)
    assert "abcdef1234567890" not in out
    assert "signature=<redacted>" in out


def test_sanitize_binance_error_text_redacts_timestamp() -> None:
    text = "ts mismatch timestamp=1700000000000"
    out = sanitize_binance_error_text(text)
    assert "1700000000000" not in out


def test_sanitize_binance_error_text_redacts_api_key_header() -> None:
    text = "X-MBX-APIKEY: my-real-api-key-1234"
    out = sanitize_binance_error_text(text)
    assert "my-real-api-key-1234" not in out


def test_http_error_message_does_not_contain_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 4xx response must not propagate signature= to the LiveBrokerError text."""
    client = _make_signed_client()
    monkeypatch.setattr(client, "_fetch_server_time_ms", lambda: time_now_ms())

    secret_signature = "REAL_SIGNATURE_ABCDEF1234567890"

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        full_url = f"{url}?symbol=BTCUSDT&timestamp=1700000000000&signature={secret_signature}"
        request = httpx.Request(method, full_url)
        return httpx.Response(
            400,
            json={"code": -1022, "msg": "Signature for this request is not valid."},
            request=request,
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(LiveBrokerError) as exc_info:
        client._signed_request("POST", "/fapi/v1/order", {"symbol": "BTCUSDT"})

    msg = str(exc_info.value)
    assert secret_signature not in msg
    assert "signature=REAL" not in msg.lower().replace("<redacted>", "")
    # Status code and sanitized path must still be present for ops.
    assert "status=400" in msg
    assert "path=/fapi/v1/order" in msg


def test_http_error_message_does_not_contain_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_signed_client()
    monkeypatch.setattr(client, "_fetch_server_time_ms", lambda: time_now_ms())

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        full_url = f"{url}?signature=zz&timestamp=1700000000000"
        return httpx.Response(
            500,
            text="server error",
            request=httpx.Request(method, full_url),
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(LiveBrokerError) as exc_info:
        client._signed_request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT"})

    msg = str(exc_info.value)
    assert "1700000000000" not in msg


def test_http_error_message_does_not_contain_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The X-MBX-APIKEY value must never appear in the LiveBrokerError text."""
    client = _make_signed_client(api_key="REAL_API_KEY_XYZ")
    monkeypatch.setattr(client, "_fetch_server_time_ms", lambda: time_now_ms())

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        # Simulate a Binance error body that echoes back the api key header.
        return httpx.Response(
            401,
            text="Unauthorized: X-MBX-APIKEY: REAL_API_KEY_XYZ",
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(LiveBrokerError) as exc_info:
        client._signed_request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT"})

    assert "REAL_API_KEY_XYZ" not in str(exc_info.value)


def test_http_error_message_does_not_contain_api_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The api secret must never appear in the LiveBrokerError text or chain."""
    secret = "SECRET_VALUE_THAT_MUST_NEVER_LEAK"
    client = _make_signed_client(api_secret=secret)
    monkeypatch.setattr(client, "_fetch_server_time_ms", lambda: time_now_ms())

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            418,
            text="teapot",
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(LiveBrokerError) as exc_info:
        client._signed_request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT"})

    err = exc_info.value
    full_dump = str(err) + repr(err.__cause__) + repr(err.__context__)
    assert secret not in full_dump


def test_http_error_message_drops_query_string_from_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path is fine to log; the query string is forbidden."""
    client = _make_signed_client()
    monkeypatch.setattr(client, "_fetch_server_time_ms", lambda: time_now_ms())

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        full_url = (
            f"{url}?symbol=BTCUSDT&recvWindow=5000&timestamp=1700000000000&signature=DEADBEEF"
        )
        return httpx.Response(
            400,
            text="bad request",
            request=httpx.Request(method, full_url),
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(LiveBrokerError) as exc_info:
        client._signed_request("POST", "/fapi/v1/order", {"symbol": "BTCUSDT"})

    msg = str(exc_info.value)
    assert "DEADBEEF" not in msg
    assert "?" not in msg
    assert "/fapi/v1/order" in msg


def test_http_error_suppresses_exception_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chained httpx exception must not leak signed URL via __cause__/__context__."""
    client = _make_signed_client()
    monkeypatch.setattr(client, "_fetch_server_time_ms", lambda: time_now_ms())

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        full_url = f"{url}?signature=ZZZZSECRET&timestamp=1700"
        return httpx.Response(400, text="x", request=httpx.Request(method, full_url))

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(LiveBrokerError) as exc_info:
        client._signed_request("POST", "/fapi/v1/order", {"symbol": "BTCUSDT"})

    err = exc_info.value
    assert err.__suppress_context__ is True
    assert err.__cause__ is None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def time_now_ms() -> int:
    import time as _time

    return int(_time.time() * 1000)
