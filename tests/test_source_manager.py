from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.data.binance_ws import WsKlineTick, WsTick
from dark_alpha_phase_one.data.datastore import DataStore
from dark_alpha_phase_one.data.source_manager import SourceManager


class FakeRestClient:
    def __init__(self) -> None:
        self.kline_calls = 0

    def fetch_price(self, symbol: str):
        return 100.0, datetime.now(tz=timezone.utc)

    def fetch_klines(self, symbol: str, limit: int):
        self.kline_calls += 1
        base = Candle(open=100, high=101, low=99, close=100)
        return [base for _ in range(limit)], datetime.now(tz=timezone.utc)


class FailingRestClient(FakeRestClient):
    def fetch_klines(self, symbol: str, limit: int):
        raise RuntimeError("rest_down")


class FakeWsClient:
    def __init__(self) -> None:
        self.connected = True
        self.raise_exc = False
        self.ticks: list[WsTick] = []
        self.kline_ticks: list[WsKlineTick] = []

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def read_events(self) -> tuple[list[WsTick], list[WsKlineTick]]:
        if self.raise_exc:
            raise RuntimeError("ws_fail")
        ticks = self.ticks
        kline_ticks = self.kline_ticks
        self.ticks = []
        self.kline_ticks = []
        return ticks, kline_ticks


def _manager(datastore: DataStore, rest: FakeRestClient, ws: FakeWsClient) -> SourceManager:
    return SourceManager(
        symbols=["BTCUSDT"],
        datastore=datastore,
        rest_client=rest,
        ws_client=ws,
        preferred_mode="ws",
        stale_seconds=5,
        kline_stale_seconds=30,
        ws_backoff_min=1,
        ws_backoff_max=60,
        rest_price_poll_seconds=0.0,
        rest_kline_poll_seconds=0.0,
        ws_recover_good_ticks=3,
        state_sync_klines=120,
    )


def test_stale_timeout_switches_to_rest() -> None:
    datastore = DataStore(symbols=["BTCUSDT"])
    rest = FakeRestClient()
    ws = FakeWsClient()
    manager = _manager(datastore, rest, ws)

    old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
    datastore.update_price("BTCUSDT", 100.0, old_ts)
    datastore.merge_klines("BTCUSDT", [Candle(open=1, high=2, low=1, close=1)], datetime.now(tz=timezone.utc))

    manager.refresh()

    assert manager.current_mode() == "rest"


def test_recovered_good_ticks_switches_back_to_ws() -> None:
    datastore = DataStore(symbols=["BTCUSDT"])
    rest = FakeRestClient()
    ws = FakeWsClient()
    manager = _manager(datastore, rest, ws)

    datastore.update_price("BTCUSDT", 100.0, datetime.now(tz=timezone.utc) - timedelta(seconds=10))
    datastore.merge_klines("BTCUSDT", [Candle(open=1, high=2, low=1, close=1)], datetime.now(tz=timezone.utc))
    manager.refresh()
    assert manager.current_mode() == "rest"

    now = datetime.now(tz=timezone.utc)
    ws.connected = True
    ws.ticks = [
        WsTick(symbol="BTCUSDT", price=101.0, ts=now),
        WsTick(symbol="BTCUSDT", price=102.0, ts=now),
        WsTick(symbol="BTCUSDT", price=103.0, ts=now),
    ]
    manager.refresh()

    assert manager.current_mode() == "ws"


def test_state_sync_called_when_recovered() -> None:
    datastore = DataStore(symbols=["BTCUSDT"])
    rest = FakeRestClient()
    ws = FakeWsClient()
    manager = _manager(datastore, rest, ws)

    datastore.update_price("BTCUSDT", 100.0, datetime.now(tz=timezone.utc) - timedelta(seconds=10))
    datastore.merge_klines("BTCUSDT", [Candle(open=1, high=2, low=1, close=1)], datetime.now(tz=timezone.utc))
    manager.refresh()
    assert manager.current_mode() == "rest"

    now = datetime.now(tz=timezone.utc)
    ws.ticks = [
        WsTick(symbol="BTCUSDT", price=101.0, ts=now),
        WsTick(symbol="BTCUSDT", price=102.0, ts=now),
        WsTick(symbol="BTCUSDT", price=103.0, ts=now),
    ]
    calls_before = rest.kline_calls
    manager.refresh()

    assert manager.current_mode() == "ws"
    assert rest.kline_calls > calls_before
    assert len(datastore.snapshot("BTCUSDT").klines_1m) >= 120


def test_bootstrap_state_sync_failure_does_not_crash_init() -> None:
    datastore = DataStore(symbols=["BTCUSDT"])
    ws = FakeWsClient()

    manager = SourceManager(
        symbols=["BTCUSDT"],
        datastore=datastore,
        rest_client=FailingRestClient(),
        ws_client=ws,
        preferred_mode="ws",
        stale_seconds=5,
        kline_stale_seconds=30,
        ws_backoff_min=1,
        ws_backoff_max=60,
        rest_price_poll_seconds=1,
        rest_kline_poll_seconds=10,
        ws_recover_good_ticks=3,
        state_sync_klines=120,
    )

    assert manager.current_mode() in {"ws", "rest"}


def test_only_closed_kline_updates_close_timestamp() -> None:
    datastore = DataStore(symbols=["BTCUSDT"])
    ts1 = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
    ts2 = ts1 + timedelta(seconds=5)

    datastore.upsert_ws_kline(
        "BTCUSDT",
        Candle(open=1, high=2, low=1, close=1.5),
        open_time_ms=1000,
        is_closed=False,
        ts=ts1,
    )
    assert datastore.snapshot("BTCUSDT").last_kline_close_ts is None

    datastore.upsert_ws_kline(
        "BTCUSDT",
        Candle(open=1, high=2, low=1, close=1.8),
        open_time_ms=1000,
        is_closed=True,
        ts=ts2,
    )
    assert datastore.snapshot("BTCUSDT").last_kline_close_ts == ts2
