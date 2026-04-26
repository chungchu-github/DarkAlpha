"""Binance USDT-M futures broker.

This module started as the Gate 2 testnet adapter. It now exposes a generic
futures broker with the same safety defaults:

- testnet remains the default
- mainnet is available only when live_safety preflight and micro-live caps pass
- signed REST requests use deterministic client order ids from live_safety
- protective orders are submitted as reduce-only conditional orders
- any partial bracket submission failure cancels all open orders for the symbol
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse

import httpx
import structlog

from strategy.schemas import ExecutionTicket, PlannedOrder

from .exchange_filters import BinanceExchangeInfoClient, ExchangeFilterProvider
from .live_safety import (
    LiveExecutionConfig,
    assert_micro_live_ticket,
    client_order_id,
    load_live_execution_config,
)

log = structlog.get_logger(__name__)

_TESTNET_BASE_URL = "https://testnet.binancefuture.com"
_MAINNET_BASE_URL = "https://fapi.binance.com"
_DEFAULT_RECV_WINDOW = 5_000
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_SERVER_TIME_TTL_SECONDS = 60.0
_DEFAULT_MAX_CLOCK_DRIFT_MS = 5_000


class LiveBrokerError(RuntimeError):
    """Raised when live broker execution must fail closed."""


# ---------------------------------------------------------------------------
# Task 5 — secret redaction helpers
# ---------------------------------------------------------------------------


def sanitize_binance_url(url: str) -> str:
    """Return only the path component of a Binance URL.

    Signed-request URLs carry ``signature=``, ``timestamp=``, and ``recvWindow=``
    in the query string. These must never appear in logs or exception messages.
    Always log the path only.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return "/<sanitized>"
    return parsed.path or "/"


_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"signature=[^&\s\"']+", re.IGNORECASE), "signature=<redacted>"),
    (re.compile(r"timestamp=\d+", re.IGNORECASE), "timestamp=<redacted>"),
    (
        re.compile(r"X-MBX-APIKEY[:\s=]+\S+", re.IGNORECASE),
        "X-MBX-APIKEY=<redacted>",
    ),
)


def sanitize_binance_error_text(text: str) -> str:
    """Scrub ``signature=``, ``timestamp=``, and ``X-MBX-APIKEY`` tokens.

    Used on Binance response bodies and any free-form error string before it is
    raised inside a ``LiveBrokerError`` (Task 5).
    """
    if not text:
        return text
    sanitized = text
    for pattern, replacement in _REDACT_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


@dataclass(frozen=True)
class LiveOrderAck:
    client_order_id: str
    exchange_order_id: str
    role: str
    symbol: str
    side: str
    type: str
    status: str
    price: float | None
    quantity: float


class BinanceFuturesClient(Protocol):
    def set_leverage(self, symbol: str, leverage: int) -> Mapping[str, Any]: ...

    def position_risk(self, symbol: str) -> list[Mapping[str, Any]]: ...

    def open_orders(self, symbol: str) -> list[Mapping[str, Any]]: ...

    def open_algo_orders(self, symbol: str) -> list[Mapping[str, Any]]: ...

    def new_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def new_algo_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]: ...

    def query_algo_order(self, symbol: str, client_algo_id: str) -> Mapping[str, Any]: ...

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]: ...

    def cancel_all_open_algo_orders(self, symbol: str) -> Mapping[str, Any]: ...


class BinanceSignedClient:
    """Small signed REST client for Binance USDT-M Futures."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = _TESTNET_BASE_URL,
        environment: str = "testnet",
        recv_window: int = _DEFAULT_RECV_WINDOW,
        timeout: float = _DEFAULT_TIMEOUT,
        server_time_ttl_seconds: float = _DEFAULT_SERVER_TIME_TTL_SECONDS,
        max_clock_drift_ms: int = _DEFAULT_MAX_CLOCK_DRIFT_MS,
    ) -> None:
        self._environment = environment
        key_name, secret_name = _credential_names(environment)
        self._api_key = api_key or os.getenv(key_name, "")
        self._api_secret = api_secret or os.getenv(secret_name, "")
        self._base_url = base_url.rstrip("/")
        self._recv_window = recv_window
        self._timeout = timeout
        # Task 4 — server time sync state. Offset is added to local epoch ms to
        # produce the Binance-correct timestamp for signed requests.
        self._server_time_ttl_seconds = server_time_ttl_seconds
        self._max_clock_drift_ms = max_clock_drift_ms
        self._server_time_offset_ms: int = 0
        self._server_time_fetched_monotonic: float | None = None

    def assert_credentials(self) -> None:
        if not self._api_key or not self._api_secret:
            raise LiveBrokerError(f"binance_{self._environment}_credentials_missing")

    # ------------------------------------------------------------------
    # Task 4 — server time sync
    # ------------------------------------------------------------------

    def _fetch_server_time_ms(self) -> int:
        """Fetch Binance server time via the unsigned ``/fapi/v1/time`` endpoint.

        Raises ``LiveBrokerError`` on any HTTP / parse failure so callers must
        fail closed rather than fall back to an untrusted local clock.
        """
        try:
            resp = httpx.get(
                f"{self._base_url}/fapi/v1/time",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            return int(payload["serverTime"])
        except httpx.HTTPError as exc:
            raise LiveBrokerError(f"server_time_unavailable type={type(exc).__name__}") from None
        except (KeyError, TypeError, ValueError) as exc:
            raise LiveBrokerError(f"server_time_unparseable type={type(exc).__name__}") from None

    def _ensure_server_time_offset(self) -> None:
        """Refresh the server-time offset if absent or older than the TTL.

        Live signed requests MUST NOT proceed without a verified offset.
        If the refresh fails or the measured drift exceeds
        ``max_clock_drift_ms`` the request is blocked.
        """
        now_monotonic = time.monotonic()
        if (
            self._server_time_fetched_monotonic is not None
            and now_monotonic - self._server_time_fetched_monotonic < self._server_time_ttl_seconds
        ):
            return

        local_before_ms = int(time.time() * 1000)
        server_ms = self._fetch_server_time_ms()
        local_after_ms = int(time.time() * 1000)
        # Use the local mid-point for a round-trip estimate.
        local_mid_ms = (local_before_ms + local_after_ms) // 2
        offset = int(server_ms) - local_mid_ms

        if abs(offset) > self._max_clock_drift_ms:
            raise LiveBrokerError(
                f"clock_drift_too_large offset_ms={offset} cap_ms={self._max_clock_drift_ms}"
            )

        self._server_time_offset_ms = offset
        self._server_time_fetched_monotonic = now_monotonic

    def _signed_timestamp_ms(self) -> int:
        """Return the local epoch ms adjusted by the cached server-time offset."""
        return int(time.time() * 1000) + self._server_time_offset_ms

    def set_leverage(self, symbol: str, leverage: int) -> Mapping[str, Any]:
        return self._signed_request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": normalize_symbol(symbol), "leverage": leverage},
        )

    def position_risk(self, symbol: str) -> list[Mapping[str, Any]]:
        payload = self._signed_request(
            "GET",
            "/fapi/v2/positionRisk",
            {"symbol": normalize_symbol(symbol)},
        )
        if not isinstance(payload, list):
            raise LiveBrokerError("binance_position_risk_unexpected_payload")
        return payload

    def open_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        payload = self._signed_request(
            "GET",
            "/fapi/v1/openOrders",
            {"symbol": normalize_symbol(symbol)},
        )
        if not isinstance(payload, list):
            raise LiveBrokerError("binance_open_orders_unexpected_payload")
        return payload

    def open_algo_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        payload = self._signed_request(
            "GET",
            "/fapi/v1/openAlgoOrders",
            {"symbol": normalize_symbol(symbol)},
        )
        if not isinstance(payload, list):
            raise LiveBrokerError("binance_open_algo_orders_unexpected_payload")
        return payload

    def new_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = self._signed_request("POST", "/fapi/v1/order", dict(params))
        if not isinstance(payload, dict):
            raise LiveBrokerError("binance_new_order_unexpected_payload")
        return payload

    def new_algo_order(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = self._signed_request("POST", "/fapi/v1/algoOrder", dict(params))
        if not isinstance(payload, dict):
            raise LiveBrokerError("binance_new_algo_order_unexpected_payload")
        return payload

    def query_order(self, symbol: str, client_order_id: str) -> Mapping[str, Any]:
        payload = self._signed_request(
            "GET",
            "/fapi/v1/order",
            {"symbol": normalize_symbol(symbol), "origClientOrderId": client_order_id},
        )
        if not isinstance(payload, dict):
            raise LiveBrokerError("binance_query_order_unexpected_payload")
        return payload

    def query_algo_order(self, symbol: str, client_algo_id: str) -> Mapping[str, Any]:
        payload = self._signed_request(
            "GET",
            "/fapi/v1/algoOrder",
            {"clientAlgoId": client_algo_id},
        )
        if not isinstance(payload, dict):
            raise LiveBrokerError("binance_query_algo_order_unexpected_payload")
        return payload

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:
        payload = self._signed_request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            {"symbol": normalize_symbol(symbol)},
        )
        if not isinstance(payload, dict):
            raise LiveBrokerError("binance_cancel_all_unexpected_payload")
        return payload

    def cancel_all_open_algo_orders(self, symbol: str) -> Mapping[str, Any]:
        payload = self._signed_request(
            "DELETE",
            "/fapi/v1/algoOpenOrders",
            {"symbol": normalize_symbol(symbol)},
        )
        if not isinstance(payload, dict):
            raise LiveBrokerError("binance_cancel_all_algo_unexpected_payload")
        return payload

    def _signed_request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any],
    ) -> Any:
        self.assert_credentials()
        # Task 4 — never sign with an unverified local clock.
        self._ensure_server_time_offset()

        request_params = {
            **params,
            "recvWindow": self._recv_window,
            "timestamp": self._signed_timestamp_ms(),
        }
        query = urlencode(request_params, doseq=True)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_params = {**request_params, "signature": signature}

        try:
            resp = httpx.request(
                method,
                f"{self._base_url}{path}",
                params=signed_params,
                headers={
                    "X-MBX-APIKEY": self._api_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            # Task 5 — never let signature/timestamp/api key leak via traceback.
            safe_path = sanitize_binance_url(str(exc.request.url)) if exc.request else path
            safe_body = sanitize_binance_error_text((exc.response.text or "")[:500])
            raise LiveBrokerError(
                f"binance_http_error status={exc.response.status_code} "
                f"path={safe_path} body={safe_body}"
            ) from None
        except httpx.HTTPError as exc:
            request_url = ""
            req = getattr(exc, "request", None)
            if req is not None:
                request_url = str(getattr(req, "url", ""))
            safe_path = sanitize_binance_url(request_url) or path
            raise LiveBrokerError(
                f"binance_request_failed type={type(exc).__name__} path={safe_path}"
            ) from None


class BinanceFuturesBroker:
    """Submit live tickets to Binance USDT-M Futures."""

    def __init__(
        self,
        *,
        client: BinanceFuturesClient | None = None,
        config: LiveExecutionConfig | None = None,
        filters: ExchangeFilterProvider | None = None,
        require_take_profit: bool = True,
    ) -> None:
        self._config = config or load_live_execution_config()
        base_url = _base_url_for_environment(self._config.environment)
        self._client = client or BinanceSignedClient(
            base_url=base_url,
            environment=self._config.environment,
        )
        self._filters = filters or BinanceExchangeInfoClient(base_url=base_url)
        self._require_take_profit = require_take_profit

    @property
    def client(self) -> BinanceFuturesClient:
        return self._client

    def submit_ticket(self, ticket: ExecutionTicket) -> list[LiveOrderAck]:
        if self._config.environment not in {"testnet", "mainnet"}:
            raise LiveBrokerError(f"unsupported_binance_environment:{self._config.environment}")
        assert_micro_live_ticket(ticket, self._config)
        self._validate_ticket(ticket)

        symbol = normalize_symbol(ticket.symbol)
        self._assert_no_existing_position(ticket.symbol)
        self._assert_no_open_orders(ticket.symbol)
        self._client.set_leverage(ticket.symbol, max(1, math.ceil(ticket.leverage)))

        acks: list[LiveOrderAck] = []
        failed_role: str | None = None
        try:
            for order in self._ordered_bracket(ticket):
                failed_role = order.role
                payload = self._order_payload(ticket, order, symbol)
                raw_ack = (
                    self._client.new_algo_order(payload)
                    if order.role in {"stop", "take_profit"}
                    else self._client.new_order(payload)
                )
                acks.append(self._ack(order, raw_ack))
            failed_role = None
        except Exception as exc:
            # Task 8 — defensive cancel sweep. Either branch may itself fail
            # (network blip, exchange overload, etc.); record that explicitly
            # without masking the original exception. The local idempotency
            # rows for partially-submitted orders stay 'reserved' until the
            # live order sync polls the exchange to heal them.
            cancel_status = "cancel_attempted"
            try:
                self._client.cancel_all_open_orders(ticket.symbol)
                self._client.cancel_all_open_algo_orders(ticket.symbol)
                cancel_status = "cancel_confirmed"
            except Exception as cancel_exc:  # noqa: BLE001
                cancel_status = "cancel_unknown"
                log.error(
                    "live_broker.cancel_after_failure_unknown",
                    ticket_id=ticket.ticket_id,
                    symbol=ticket.symbol,
                    cancel_error=str(cancel_exc),
                )
            log.error(
                "live_broker.submit_failed",
                ticket_id=ticket.ticket_id,
                symbol=ticket.symbol,
                failed_role=failed_role,
                partial_acks=len(acks),
                cancel_status=cancel_status,
                error=str(exc),
            )
            if isinstance(exc, LiveBrokerError):
                raise
            raise LiveBrokerError(
                f"binance_submit_failed type={type(exc).__name__} "
                f"failed_role={failed_role} cancel={cancel_status}"
            ) from None

        log.info(
            "live_broker.ticket_submitted",
            ticket_id=ticket.ticket_id,
            symbol=ticket.symbol,
            orders=len(acks),
        )
        return acks

    def cancel_all_open_orders(self, symbol: str) -> Mapping[str, Any]:
        regular = self._client.cancel_all_open_orders(symbol)
        algo = self._client.cancel_all_open_algo_orders(symbol)
        return {"regular": regular, "algo": algo}

    def emergency_close_symbol(self, symbol: str) -> LiveOrderAck | None:
        if self._config.environment not in {"testnet", "mainnet"}:
            raise LiveBrokerError(f"unsupported_binance_environment:{self._config.environment}")
        rows = self._client.position_risk(symbol)
        position_amt = 0.0
        for row in rows:
            position_amt += float(row.get("positionAmt", 0) or 0)
        if position_amt == 0:
            return None

        side = "sell" if position_amt > 0 else "buy"
        quantity = abs(position_amt)
        payload = self.emergency_close_payload(symbol, side, quantity)
        raw_ack = self._client.new_order(payload)
        return LiveOrderAck(
            client_order_id=str(raw_ack.get("clientOrderId") or payload["newClientOrderId"]),
            exchange_order_id=str(raw_ack.get("orderId") or ""),
            role="emergency_close",
            symbol=str(raw_ack.get("symbol") or normalize_symbol(symbol)),
            side=str(raw_ack.get("side") or side.upper()),
            type=str(raw_ack.get("type") or "MARKET"),
            status=str(raw_ack.get("status") or "NEW"),
            price=None,
            quantity=float(raw_ack["origQty"])
            if raw_ack.get("origQty") not in (None, "")
            else quantity,
        )

    def emergency_close_payload(self, symbol: str, side: str, quantity: float) -> dict[str, Any]:
        cid = f"DACLOSE{normalize_symbol(symbol)}{int(time.time())}"[:36]
        filters = self._filters.symbol_filters(symbol)
        return {
            "symbol": normalize_symbol(symbol),
            "side": side.upper(),
            "type": "MARKET",
            "quantity": filters.quantity(quantity),
            "reduceOnly": "true",
            "newClientOrderId": cid,
        }

    def _validate_ticket(self, ticket: ExecutionTicket) -> None:
        if ticket.shadow_mode:
            raise LiveBrokerError("live_broker_received_shadow_ticket")
        if ticket.quantity <= 0:
            raise LiveBrokerError("invalid_quantity")
        if ticket.risk_usd <= 0:
            raise LiveBrokerError("missing_risk_usd")
        if ticket.stop_price <= 0:
            raise LiveBrokerError("missing_stop_price")
        if ticket.leverage <= 0:
            raise LiveBrokerError("invalid_leverage")

        roles = {order.role for order in ticket.orders}
        required = {"entry", "stop"}
        if self._require_take_profit:
            required.add("take_profit")
        missing = required - roles
        if missing:
            raise LiveBrokerError(f"missing_order_roles:{','.join(sorted(missing))}")

        for order in ticket.orders:
            if order.role in {"stop", "take_profit"} and not order.reduce_only:
                raise LiveBrokerError(f"exit_order_must_be_reduce_only:{order.role}")
            if order.quantity <= 0:
                raise LiveBrokerError(f"invalid_order_quantity:{order.role}")

    def _assert_no_existing_position(self, symbol: str) -> None:
        rows = self._client.position_risk(symbol)
        for row in rows:
            amt = float(row.get("positionAmt", 0) or 0)
            if abs(amt) > 0:
                raise LiveBrokerError("existing_exchange_position")

    def _assert_no_open_orders(self, symbol: str) -> None:
        rows = self._client.open_orders(symbol)
        if rows:
            raise LiveBrokerError("existing_exchange_open_orders")
        algo_rows = self._client.open_algo_orders(symbol)
        if algo_rows:
            raise LiveBrokerError("existing_exchange_open_algo_orders")

    @staticmethod
    def _ordered_bracket(ticket: ExecutionTicket) -> list[PlannedOrder]:
        order_by_role = {order.role: order for order in ticket.orders}
        ordered = [order_by_role["entry"], order_by_role["stop"]]
        take_profit = order_by_role.get("take_profit")
        if take_profit is not None:
            ordered.append(take_profit)
        return ordered

    def _order_payload(
        self,
        ticket: ExecutionTicket,
        order: PlannedOrder,
        symbol: str,
    ) -> dict[str, Any]:
        cid = client_order_id(ticket, order)
        filters = self._filters.symbol_filters(symbol)
        quantity = filters.quantity(order.quantity)
        payload: dict[str, Any] = {
            "symbol": symbol,
            "side": order.side.upper(),
            "quantity": quantity,
        }
        if order.role == "entry" and order.type == "limit":
            if order.price is None:
                raise LiveBrokerError("limit_entry_missing_price")
            filters.assert_min_notional(price=order.price, quantity=float(quantity))
            payload.update(
                {
                    "newClientOrderId": cid,
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "price": filters.price(order.price),
                }
            )
            return payload
        if order.role == "stop":
            if order.price is None:
                raise LiveBrokerError("stop_order_missing_price")
            payload.update(
                {
                    "algoType": "CONDITIONAL",
                    "clientAlgoId": cid,
                    "type": "STOP_MARKET",
                    "triggerPrice": filters.price(order.price),
                    "reduceOnly": "true",
                    "workingType": "MARK_PRICE",
                }
            )
            return payload
        if order.role == "take_profit":
            if order.price is None:
                raise LiveBrokerError("take_profit_missing_price")
            payload.update(
                {
                    "algoType": "CONDITIONAL",
                    "clientAlgoId": cid,
                    "type": "TAKE_PROFIT_MARKET",
                    "triggerPrice": filters.price(order.price),
                    "reduceOnly": "true",
                    "workingType": "MARK_PRICE",
                }
            )
            return payload
        raise LiveBrokerError(f"unsupported_order_role:{order.role}")

    @staticmethod
    def _ack(order: PlannedOrder, payload: Mapping[str, Any]) -> LiveOrderAck:
        return LiveOrderAck(
            client_order_id=str(
                payload.get("clientOrderId")
                or payload.get("clientAlgoId")
                or payload.get("newClientOrderId")
                or ""
            ),
            exchange_order_id=str(payload.get("orderId") or payload.get("algoId") or ""),
            role=order.role,
            symbol=str(payload.get("symbol") or normalize_symbol(order.symbol)),
            side=str(payload.get("side") or order.side.upper()),
            type=str(payload.get("type") or order.type.upper()),
            status=str(payload.get("status") or payload.get("algoStatus") or "NEW"),
            price=float(payload["price"])
            if payload.get("price") not in (None, "")
            else order.price,
            quantity=float(payload.get("origQty") or payload.get("quantity") or order.quantity),
        )


def normalize_symbol(symbol: str) -> str:
    for suffix in ("-PERP", "_PERP"):
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def format_price(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


class BinanceTestnetBroker(BinanceFuturesBroker):
    """Compatibility wrapper that refuses non-testnet config."""

    def submit_ticket(self, ticket: ExecutionTicket) -> list[LiveOrderAck]:
        if self._config.environment != "testnet":
            raise LiveBrokerError("binance_testnet_broker_refuses_non_testnet")
        return super().submit_ticket(ticket)

    def emergency_close_symbol(self, symbol: str) -> LiveOrderAck | None:
        if self._config.environment != "testnet":
            raise LiveBrokerError("binance_testnet_broker_refuses_non_testnet")
        return super().emergency_close_symbol(symbol)


def _base_url_for_environment(environment: str) -> str:
    if environment == "mainnet":
        return _MAINNET_BASE_URL
    return _TESTNET_BASE_URL


def _credential_names(environment: str) -> tuple[str, str]:
    if environment == "mainnet":
        return "BINANCE_FUTURES_MAINNET_API_KEY", "BINANCE_FUTURES_MAINNET_API_SECRET"
    return "BINANCE_FUTURES_TESTNET_API_KEY", "BINANCE_FUTURES_TESTNET_API_SECRET"


def format_quantity(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")
