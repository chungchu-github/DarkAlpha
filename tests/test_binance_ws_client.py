from __future__ import annotations

import json
from queue import Queue

from dark_alpha_phase_one.data.binance_ws import BinanceWsClient


class FakeTimeoutError(TimeoutError):
    pass


class FakeSocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages: Queue[str] = Queue()
        for item in messages:
            self.messages.put(item)
        self.connected_url: str | None = None
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, url: str) -> None:
        self.connected_url = url

    def recv(self) -> str:
        if self.messages.empty():
            raise FakeTimeoutError("timeout")
        return self.messages.get()

    def close(self) -> None:
        return None


def test_ws_client_parses_bookticker_and_kline_events() -> None:
    messages = [
        json.dumps(
            {
                "stream": "btcusdt@bookTicker",
                "data": {"e": "bookTicker", "E": 1700000000000, "s": "BTCUSDT", "b": "100", "a": "102"},
            }
        ),
        json.dumps(
            {
                "stream": "btcusdt@kline_1m",
                "data": {
                    "e": "kline",
                    "E": 1700000001000,
                    "s": "BTCUSDT",
                    "k": {"t": 1700000000000, "o": "99", "h": "103", "l": "98", "c": "101", "x": True},
                },
            }
        ),
    ]
    socket = FakeSocket(messages)
    client = BinanceWsClient(symbols=["BTCUSDT"], ws_factory=lambda: socket)

    client.connect()
    ticks, kline_ticks = client.read_events()

    assert socket.connected_url is not None
    assert "btcusdt@bookTicker" in socket.connected_url
    assert len(ticks) == 1
    assert ticks[0].price == 101.0
    assert len(kline_ticks) == 1
    assert kline_ticks[0].candle.close == 101.0
    assert kline_ticks[0].is_closed is True


def test_ws_client_close_marks_disconnected() -> None:
    socket = FakeSocket([])
    client = BinanceWsClient(symbols=["BTCUSDT"], ws_factory=lambda: socket)

    client.connect()
    assert client.connected is True
    client.close()
    assert client.connected is False
