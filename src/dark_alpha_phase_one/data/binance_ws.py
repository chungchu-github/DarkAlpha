from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging


@dataclass(frozen=True)
class WsTick:
    symbol: str
    price: float
    ts: datetime


class BinanceWsClient:
    """Best-effort websocket client abstraction.

    MVP keeps this lightweight and mock-friendly; production websocket wire-up can
    be added behind the same interface without changing source-manager logic.
    """

    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        logging.info("WS connected for symbols: %s", self.symbols)

    def close(self) -> None:
        self.connected = False
        logging.warning("WS connection closed")

    def read_price_ticks(self) -> list[WsTick]:
        if not self.connected:
            raise RuntimeError("ws_not_connected")
        # Placeholder for real websocket streaming implementation.
        return []

    @staticmethod
    def now_tick(symbol: str, price: float) -> WsTick:
        return WsTick(symbol=symbol, price=price, ts=datetime.now(tz=timezone.utc))
