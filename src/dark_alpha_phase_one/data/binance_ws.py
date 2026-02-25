from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable

from dark_alpha_phase_one.calculations import Candle


@dataclass(frozen=True)
class WsTick:
    symbol: str
    price: float
    ts: datetime


@dataclass(frozen=True)
class WsKlineTick:
    symbol: str
    candle: Candle
    open_time_ms: int
    ts: datetime


class BinanceWsClient:
    def __init__(
        self,
        symbols: list[str],
        read_timeout_seconds: float = 0.2,
        ws_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.symbols = symbols
        self.read_timeout_seconds = read_timeout_seconds
        self._ws_factory = ws_factory or self._default_ws_factory
        self._ws: Any | None = None
        self.connected = False

    def connect(self) -> None:
        stream_parts: list[str] = []
        for symbol in self.symbols:
            lower = symbol.lower()
            stream_parts.append(f"{lower}@bookTicker")
            stream_parts.append(f"{lower}@kline_1m")
        streams = "/".join(stream_parts)
        url = f"wss://fstream.binance.com/stream?streams={streams}"

        ws = self._ws_factory()
        ws.settimeout(self.read_timeout_seconds)
        ws.connect(url)
        self._ws = ws
        self.connected = True
        logging.info("WS connected stream=%s", streams)

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws = None
        self.connected = False
        logging.warning("WS connection closed")

    def read_events(self) -> tuple[list[WsTick], list[WsKlineTick]]:
        if not self.connected or self._ws is None:
            raise RuntimeError("ws_not_connected")

        ticks: list[WsTick] = []
        kline_ticks: list[WsKlineTick] = []

        while True:
            try:
                raw = self._ws.recv()
            except Exception as exc:  # includes timeout/non-blocking behavior
                if self._is_timeout_exception(exc):
                    break
                raise

            message = self._safe_parse(raw)
            if not message:
                continue

            data = message.get("data", message)
            event_type = data.get("e")
            event_ts = self._event_ts(data)

            if event_type == "bookTicker":
                symbol = str(data.get("s", "")).upper()
                bid = self._to_float(data.get("b"))
                ask = self._to_float(data.get("a"))
                if symbol and (bid is not None or ask is not None):
                    if bid is not None and ask is not None:
                        price = (bid + ask) / 2.0
                    else:
                        price = bid if bid is not None else ask
                    ticks.append(WsTick(symbol=symbol, price=float(price), ts=event_ts))
            elif event_type == "kline":
                kline_data = data.get("k", {})
                symbol = str(data.get("s", "")).upper()
                candle = self._kline_to_candle(kline_data)
                open_time = kline_data.get("t")
                if symbol and candle and isinstance(open_time, int):
                    kline_ticks.append(
                        WsKlineTick(symbol=symbol, candle=candle, open_time_ms=open_time, ts=event_ts)
                    )

        return ticks, kline_ticks

    @staticmethod
    def _safe_parse(raw: Any) -> dict[str, Any] | None:
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _kline_to_candle(self, payload: dict[str, Any]) -> Candle | None:
        open_ = self._to_float(payload.get("o"))
        high = self._to_float(payload.get("h"))
        low = self._to_float(payload.get("l"))
        close = self._to_float(payload.get("c"))
        if None in (open_, high, low, close):
            return None
        return Candle(open=open_, high=high, low=low, close=close)

    @staticmethod
    def _event_ts(data: dict[str, Any]) -> datetime:
        raw = data.get("E")
        if isinstance(raw, int):
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
        return datetime.now(tz=timezone.utc)

    @staticmethod
    def _is_timeout_exception(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        return "timeout" in name or "would block" in str(exc).lower()

    @staticmethod
    def _default_ws_factory() -> Any:
        try:
            import websocket
        except ImportError as exc:  # noqa: F401
            raise RuntimeError("websocket-client package is required for WS mode") from exc
        return websocket.WebSocket()
