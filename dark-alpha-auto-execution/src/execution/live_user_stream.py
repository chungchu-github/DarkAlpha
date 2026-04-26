"""Binance Futures user data stream ingestion.

Gate 6.3 makes live fills event-driven. Polling and reconciliation remain as a
backup, but ORDER_TRADE_UPDATE events now update local orders and positions as
soon as Binance publishes them.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

from storage.db import get_db
from strategy.schemas import ExecutionTicket

from .binance_testnet_broker import _base_url_for_environment, normalize_symbol
from .live_event_guard import LiveEventGuard
from .live_order_sync import _map_idempotency_status
from .live_safety import load_live_execution_config
from .position_manager import PositionManager

log = structlog.get_logger(__name__)

_MAINNET_WS_BASE = "wss://fstream.binance.com/ws"
_TESTNET_WS_BASE = "wss://stream.binancefuture.com/ws"


class UserStreamError(RuntimeError):
    """Raised when user stream setup or ingestion fails."""


@dataclass(frozen=True)
class UserStreamIngestResult:
    event_id: str
    event_type: str
    client_order_id: str
    symbol: str
    execution_type: str
    exchange_status: str
    local_status: str
    fill_delta: float
    cumulative_filled: float
    average_price: float | None
    action: str


class BinanceUserStreamClient:
    """Small REST client for Futures listenKey lifecycle."""

    def __init__(
        self,
        *,
        environment: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        config = load_live_execution_config()
        self._environment = environment or config.environment
        self._base_url = (base_url or _base_url_for_environment(self._environment)).rstrip("/")
        self._api_key = api_key or os.getenv(_key_name(self._environment), "")
        self._timeout = timeout

    def create_listen_key(self) -> str:
        payload = self._request("POST")
        key = str(payload.get("listenKey") or "")
        if not key:
            raise UserStreamError("binance_listen_key_missing")
        return key

    def keepalive(self, listen_key: str) -> None:
        self._request("PUT", {"listenKey": listen_key})

    def close(self, listen_key: str) -> None:
        self._request("DELETE", {"listenKey": listen_key})

    def websocket_url(self, listen_key: str) -> str:
        base = _MAINNET_WS_BASE if self._environment == "mainnet" else _TESTNET_WS_BASE
        return f"{base}/{listen_key}"

    def _request(self, method: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        if not self._api_key:
            raise UserStreamError(f"binance_{self._environment}_api_key_missing")
        try:
            response = httpx.request(
                method,
                f"{self._base_url}/fapi/v1/listenKey",
                params=params,
                headers={"X-MBX-APIKEY": self._api_key},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json() if response.content else {}
            if not isinstance(payload, dict):
                raise UserStreamError("binance_listen_key_unexpected_payload")
            return payload
        except httpx.HTTPStatusError as exc:
            raise UserStreamError(
                f"binance_listen_key_http_error:{exc.response.status_code}:{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise UserStreamError(f"binance_listen_key_request_failed:{exc}") from exc


class LiveUserStreamIngestor:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        manager: PositionManager | None = None,
        guard: LiveEventGuard | None = None,
    ) -> None:
        self._db_path = db_path
        self._manager = manager or PositionManager(db_path=db_path)
        self._guard = guard or LiveEventGuard(db_path=db_path)

    def process_event(self, payload: dict[str, Any]) -> UserStreamIngestResult | None:
        event_type = str(payload.get("e") or payload.get("eventType") or "")
        if event_type != "ORDER_TRADE_UPDATE":
            return None
        order = payload.get("o")
        if not isinstance(order, dict):
            raise UserStreamError("order_trade_update_missing_order")

        client_order_id = str(order.get("c") or "")
        if not client_order_id:
            raise UserStreamError("order_trade_update_missing_client_order_id")
        symbol = _local_symbol(str(order.get("s") or ""))
        execution_type = str(order.get("x") or "")
        exchange_status = str(order.get("X") or "").upper()
        cumulative_filled = _float(order.get("z"))
        last_filled = _float(order.get("l"))
        average_price = _avg_price(order)
        event_id = _event_id(payload, order, client_order_id)

        if not self._record_event_once(
            event_id=event_id,
            event_type=event_type,
            symbol=symbol,
            client_order_id=client_order_id,
            execution_type=execution_type,
            order_status=exchange_status,
            trade_id=str(order.get("t") or ""),
            payload=payload,
        ):
            return UserStreamIngestResult(
                event_id=event_id,
                event_type=event_type,
                client_order_id=client_order_id,
                symbol=symbol,
                execution_type=execution_type,
                exchange_status=exchange_status,
                local_status="duplicate",
                fill_delta=0.0,
                cumulative_filled=cumulative_filled,
                average_price=average_price,
                action="duplicate_ignored",
            )

        local_status = _map_idempotency_status(exchange_status)
        context = self._order_context(client_order_id)
        previous_filled = (
            float(context.get("previous_filled") or 0.0)
            if context
            else self._existing_order_filled(client_order_id)
        )
        fill_delta = max(cumulative_filled - previous_filled, last_filled, 0.0)

        if context is not None:
            self._update_known_order(
                client_order_id=client_order_id,
                local_status=local_status,
                exchange_status=exchange_status,
                fill_quantity=cumulative_filled,
                average_price=average_price,
            )
            ticket = ExecutionTicket.model_validate_json(str(context["ticket_payload"]))
            self._manager.apply_live_order_update(
                ticket=ticket,
                order_role=str(context["order_role"]),
                client_order_id=client_order_id,
                cumulative_filled=cumulative_filled,
                fill_delta=fill_delta,
                average_price=average_price,
                local_status=local_status,
            )
            if fill_delta > 0:
                self._guard.inspect_ticket_after_fill(ticket, str(context["order_role"]))
            action = f"known_order:{context['order_role']}"
        elif client_order_id.startswith("DACLOSE") and fill_delta > 0:
            self._record_unplanned_order(
                client_order_id=client_order_id,
                order=order,
                symbol=symbol,
                exchange_status=exchange_status,
                fill_quantity=cumulative_filled,
                average_price=average_price,
            )
            self._manager.apply_live_symbol_exit(
                symbol=symbol,
                client_order_id=client_order_id,
                fill_delta=fill_delta,
                average_price=average_price,
                reason="manual",
            )
            self._guard.record_untracked_fill(
                symbol=symbol,
                client_order_id=client_order_id,
                fill_delta=fill_delta,
                allow_emergency_close=True,
            )
            action = "emergency_close"
        else:
            self._record_unplanned_order(
                client_order_id=client_order_id,
                order=order,
                symbol=symbol,
                exchange_status=exchange_status,
                fill_quantity=cumulative_filled,
                average_price=average_price,
            )
            self._guard.record_untracked_fill(
                symbol=symbol,
                client_order_id=client_order_id,
                fill_delta=fill_delta,
                allow_emergency_close=False,
            )
            action = "untracked_order_recorded"

        log.info(
            "live_user_stream.event_processed",
            event_id=event_id,
            client_order_id=client_order_id,
            symbol=symbol,
            action=action,
            status=exchange_status,
        )
        return UserStreamIngestResult(
            event_id=event_id,
            event_type=event_type,
            client_order_id=client_order_id,
            symbol=symbol,
            execution_type=execution_type,
            exchange_status=exchange_status,
            local_status=local_status,
            fill_delta=fill_delta,
            cumulative_filled=cumulative_filled,
            average_price=average_price,
            action=action,
        )

    def _record_event_once(
        self,
        *,
        event_id: str,
        event_type: str,
        symbol: str,
        client_order_id: str,
        execution_type: str,
        order_status: str,
        trade_id: str,
        payload: dict[str, Any],
    ) -> bool:
        with get_db(self._db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO live_stream_events
                        (event_id, event_type, symbol, client_order_id,
                         execution_type, order_status, trade_id, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        event_type,
                        symbol,
                        client_order_id,
                        execution_type,
                        order_status,
                        trade_id,
                        json.dumps(payload, sort_keys=True),
                    ),
                )
                conn.commit()
                return True
            except Exception as exc:  # noqa: BLE001
                if "UNIQUE constraint failed" in str(exc):
                    return False
                raise

    def _order_context(self, client_order_id: str) -> dict[str, object] | None:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT oi.order_role,
                       et.payload AS ticket_payload,
                       COALESCE(o.fill_quantity, 0) AS previous_filled
                  FROM order_idempotency oi
                  JOIN execution_tickets et ON et.ticket_id = oi.ticket_id
                  LEFT JOIN orders o ON o.order_id = oi.client_order_id
                 WHERE oi.client_order_id=?
                """,
                (client_order_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "order_role": row["order_role"],
            "ticket_payload": row["ticket_payload"],
            "previous_filled": row["previous_filled"],
        }

    def _existing_order_filled(self, client_order_id: str) -> float:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(fill_quantity, 0) AS previous_filled FROM orders WHERE order_id=?",
                (client_order_id,),
            ).fetchone()
        if row is None:
            return 0.0
        return float(row["previous_filled"] or 0.0)

    def _update_known_order(
        self,
        *,
        client_order_id: str,
        local_status: str,
        exchange_status: str,
        fill_quantity: float,
        average_price: float | None,
    ) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                UPDATE order_idempotency
                   SET status=?,
                       updated_at=datetime('now')
                 WHERE client_order_id=?
                """,
                (local_status, client_order_id),
            )
            conn.execute(
                """
                UPDATE orders
                   SET status=?,
                       filled_at=CASE WHEN ? IN ('filled','cancelled','rejected') THEN datetime('now') ELSE filled_at END,
                       fill_price=?,
                       fill_quantity=?
                 WHERE order_id=?
                """,
                (
                    exchange_status.lower() or local_status,
                    local_status,
                    average_price,
                    fill_quantity,
                    client_order_id,
                ),
            )
            conn.commit()

    def _record_unplanned_order(
        self,
        *,
        client_order_id: str,
        order: dict[str, Any],
        symbol: str,
        exchange_status: str,
        fill_quantity: float,
        average_price: float | None,
    ) -> None:
        with get_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO orders
                    (order_id, ticket_id, exchange_order_id, side, type, symbol,
                     price, quantity, status, submitted_at, fill_price, fill_quantity)
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_order_id,
                    str(order.get("i") or ""),
                    str(order.get("S") or "").lower(),
                    str(order.get("o") or ""),
                    normalize_symbol(symbol),
                    _float(order.get("p")) or None,
                    _float(order.get("q")),
                    exchange_status.lower(),
                    datetime.now(tz=UTC).isoformat(),
                    average_price,
                    fill_quantity,
                ),
            )
            conn.execute(
                """
                UPDATE orders
                   SET status=?,
                       filled_at=CASE WHEN ? IN ('FILLED','CANCELED','REJECTED') THEN datetime('now') ELSE filled_at END,
                       fill_price=?,
                       fill_quantity=?
                 WHERE order_id=?
                """,
                (
                    exchange_status.lower(),
                    exchange_status,
                    average_price,
                    fill_quantity,
                    client_order_id,
                ),
            )
            conn.commit()


async def run_user_stream(
    *,
    once: bool = False,
    client: BinanceUserStreamClient | None = None,
    ingestor: LiveUserStreamIngestor | None = None,
    keepalive_seconds: int = 30 * 60,
    reconnect_delay_seconds: float = 5.0,
    max_reconnect_delay_seconds: float = 60.0,
) -> None:
    """Run the Binance user data WebSocket consumer."""
    import websockets

    client = client or BinanceUserStreamClient()
    ingestor = ingestor or LiveUserStreamIngestor()
    reconnect_delay = reconnect_delay_seconds
    while True:
        listen_key = client.create_listen_key()
        _record_runtime_heartbeat(
            component="user_stream",
            status="listen_key_created",
            details={"listen_key_suffix": listen_key[-6:]},
        )
        keepalive_task = asyncio.create_task(_keepalive_loop(client, listen_key, keepalive_seconds))
        try:
            async with websockets.connect(client.websocket_url(listen_key), ping_interval=20) as ws:
                _record_runtime_heartbeat(component="user_stream", status="connected")
                reconnect_delay = reconnect_delay_seconds
                while True:
                    raw = await ws.recv()
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        continue
                    result = ingestor.process_event(payload)
                    if result is not None:
                        _record_runtime_heartbeat(
                            component="user_stream",
                            status="event_ingested",
                            details={
                                "client_order_id": result.client_order_id,
                                "action": result.action,
                                "exchange_status": result.exchange_status,
                            },
                        )
                        log.info(
                            "live_user_stream.ingested",
                            client_order_id=result.client_order_id,
                            action=result.action,
                            status=result.exchange_status,
                        )
                    if once:
                        return
                    await asyncio.sleep(0)
        except Exception as exc:
            if once:
                raise
            log.warning(
                "live_user_stream.disconnected_reconnecting",
                error=str(exc),
                delay_seconds=reconnect_delay,
            )
            _record_runtime_heartbeat(
                component="user_stream",
                status="disconnected",
                details={"error": str(exc), "reconnect_delay_seconds": reconnect_delay},
            )
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay_seconds)
        finally:
            keepalive_task.cancel()
            with suppress(asyncio.CancelledError):
                await keepalive_task
            with suppress(UserStreamError):
                client.close(listen_key)


async def _keepalive_loop(
    client: BinanceUserStreamClient, listen_key: str, keepalive_seconds: int
) -> None:
    while True:
        await asyncio.sleep(keepalive_seconds)
        client.keepalive(listen_key)
        _record_runtime_heartbeat(component="user_stream", status="listen_key_keepalive")
        log.info("live_user_stream.listen_key_keepalive")


def _record_runtime_heartbeat(
    *,
    component: str,
    status: str,
    details: dict[str, object] | None = None,
) -> None:
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO live_runtime_heartbeats (component, status, details)
                VALUES (?, ?, ?)
                """,
                (component, status, json.dumps(details or {}, sort_keys=True)),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("live_user_stream.heartbeat_failed", error=str(exc))


def _event_id(payload: dict[str, Any], order: dict[str, Any], client_order_id: str) -> str:
    trade_id = str(order.get("t") or "")
    event_time = str(payload.get("E") or "")
    order_status = str(order.get("X") or "")
    execution_type = str(order.get("x") or "")
    cumulative = str(order.get("z") or "")
    return f"{event_time}:{client_order_id}:{trade_id}:{execution_type}:{order_status}:{cumulative}"


def _avg_price(order: dict[str, Any]) -> float | None:
    avg = _float(order.get("ap"))
    if avg > 0:
        return avg
    last_price = _float(order.get("L"))
    if last_price > 0:
        return last_price
    return None


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _local_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    return f"{normalized}-PERP"


def _key_name(environment: str) -> str:
    if environment == "mainnet":
        return "BINANCE_FUTURES_MAINNET_API_KEY"
    return "BINANCE_FUTURES_TESTNET_API_KEY"
