from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from typing import Protocol

from dark_alpha_phase_one.data.datastore import DataStore


class RestClientProtocol(Protocol):
    def fetch_price(self, symbol: str): ...

    def fetch_klines(self, symbol: str, limit: int): ...


class WsClientProtocol(Protocol):
    connected: bool

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def read_events(self) -> tuple[list, list]: ...


class SourceManager:
    def __init__(
        self,
        *,
        symbols: list[str],
        datastore: DataStore,
        rest_client: RestClientProtocol,
        ws_client: WsClientProtocol,
        preferred_mode: str,
        stale_seconds: int,
        kline_stale_seconds: int,
        ws_backoff_min: int,
        ws_backoff_max: int,
        rest_price_poll_seconds: float,
        rest_kline_poll_seconds: float,
        ws_recover_good_ticks: int,
        state_sync_klines: int,
    ) -> None:
        self.symbols = symbols
        self.datastore = datastore
        self.rest_client = rest_client
        self.ws_client = ws_client
        self.preferred_mode = preferred_mode
        self.stale_seconds = stale_seconds
        self.kline_stale_seconds = kline_stale_seconds
        self.ws_backoff_min = ws_backoff_min
        self.ws_backoff_max = ws_backoff_max
        self.rest_price_poll_seconds = rest_price_poll_seconds
        self.rest_kline_poll_seconds = rest_kline_poll_seconds
        self.ws_recover_good_ticks = ws_recover_good_ticks
        self.state_sync_klines = state_sync_klines

        self._mode = "ws" if preferred_mode == "ws" else "rest"
        self._ws_good_ticks = 0
        self._last_rest_price_poll = 0.0
        self._last_rest_kline_poll = 0.0
        self._last_health_log = 0.0
        self._ws_backoff = ws_backoff_min
        self._ws_next_retry_at = 0.0

        self.datastore.set_mode(self._mode)
        self._safe_state_sync(reason="bootstrap")
        if self._mode == "ws":
            self._connect_ws(initial=True)

    def _connect_ws(self, *, initial: bool = False) -> None:
        try:
            self.ws_client.connect()
            if initial:
                logging.info("WS initial connect ok")
        except Exception as exc:  # noqa: BLE001
            self._mode = "rest"
            self.datastore.set_mode("rest")
            self._ws_next_retry_at = time.monotonic() + self._ws_backoff
            self._ws_backoff = min(self._ws_backoff * 2, self.ws_backoff_max)
            logging.warning("WS initial connect failed, fallback to rest: %s", exc)

    def current_mode(self) -> str:
        return self._mode

    def refresh(self) -> None:
        now = datetime.now(tz=timezone.utc)
        now_mono = time.monotonic()

        self._attempt_ws_events()
        self._evaluate_staleness(now)

        if self._mode == "rest":
            self._poll_rest_prices(now_mono)
            self._poll_rest_klines(now_mono)
            self._attempt_ws_recover(now_mono)

        self._log_health_if_needed(now_mono, now)

    def _attempt_ws_events(self) -> None:
        if self._mode != "ws" or not self.ws_client.connected:
            return
        try:
            ticks, kline_ticks = self.ws_client.read_events()
            self._apply_ws_events(ticks, kline_ticks)
        except Exception as exc:  # noqa: BLE001
            self._switch_mode("rest", symbol="*", reason=f"exception:{exc}")
            self.ws_client.close()

    def _apply_ws_events(self, ticks: list, kline_ticks: list) -> int:
        fresh_ticks = 0
        now = datetime.now(tz=timezone.utc)
        for tick in ticks:
            self.datastore.update_price(tick.symbol, tick.price, tick.ts)
            age = (now - tick.ts).total_seconds()
            if age <= self.stale_seconds:
                fresh_ticks += 1
        for kline_tick in kline_ticks:
            self.datastore.upsert_ws_kline(
                kline_tick.symbol,
                kline_tick.candle,
                kline_tick.open_time_ms,
                kline_tick.is_closed,
                kline_tick.ts,
            )
        return fresh_ticks

    def _poll_rest_prices(self, now_mono: float) -> None:
        if now_mono - self._last_rest_price_poll < self.rest_price_poll_seconds:
            return
        for symbol in self.symbols:
            price, ts = self.rest_client.fetch_price(symbol)
            self.datastore.update_price(symbol, price, ts)
        self._last_rest_price_poll = now_mono

    def _poll_rest_klines(self, now_mono: float) -> None:
        if now_mono - self._last_rest_kline_poll < self.rest_kline_poll_seconds:
            return
        self._state_sync_from_rest(reason="rest_poll", limit=max(120, self.state_sync_klines))
        self._last_rest_kline_poll = now_mono

    def _attempt_ws_recover(self, now_mono: float) -> None:
        if self.preferred_mode != "ws":
            return
        if now_mono < self._ws_next_retry_at:
            return

        if not self.ws_client.connected:
            try:
                self.ws_client.connect()
                self._ws_backoff = self.ws_backoff_min
            except Exception as exc:  # noqa: BLE001
                self._ws_next_retry_at = now_mono + self._ws_backoff
                self._ws_backoff = min(self._ws_backoff * 2, self.ws_backoff_max)
                logging.warning("WS reconnect failed: %s", exc)
                return

        try:
            ticks, kline_ticks = self.ws_client.read_events()
        except Exception as exc:  # noqa: BLE001
            self.ws_client.close()
            self._ws_next_retry_at = now_mono + self._ws_backoff
            self._ws_backoff = min(self._ws_backoff * 2, self.ws_backoff_max)
            logging.warning("WS recover read failed: %s", exc)
            return

        fresh_ticks = self._apply_ws_events(ticks, kline_ticks)
        if fresh_ticks > 0:
            self._ws_good_ticks += fresh_ticks

        if self._ws_good_ticks >= self.ws_recover_good_ticks:
            if self._safe_state_sync(reason="recovered"):
                self._switch_mode("ws", symbol="*", reason="recovered")
                self._ws_good_ticks = 0

    def _safe_state_sync(self, reason: str) -> bool:
        try:
            self._state_sync_from_rest(reason=reason, limit=self.state_sync_klines)
            return True
        except Exception as exc:  # noqa: BLE001
            logging.warning("State sync failed (%s): %s", reason, exc)
            return False

    def _state_sync_from_rest(self, reason: str, limit: int) -> None:
        for symbol in self.symbols:
            klines, ts = self.rest_client.fetch_klines(symbol, limit=limit)
            self.datastore.merge_klines(symbol, klines, ts)
            logging.info("State sync (%s) for %s with %d klines", reason, symbol, len(klines))

    def _evaluate_staleness(self, now: datetime) -> None:
        if self._mode != "ws":
            return

        for symbol in self.symbols:
            snap = self.datastore.snapshot(symbol)
            price_age = self._age_seconds(now, snap.last_price_ts)
            kline_age = self._age_seconds(now, snap.last_kline_close_ts)
            if price_age is not None and price_age > self.stale_seconds:
                self._switch_mode("rest", symbol=symbol, reason="stale")
                return
            if kline_age is not None and kline_age > self.kline_stale_seconds:
                self._switch_mode("rest", symbol=symbol, reason="kline_stale")
                return

    def _switch_mode(self, to_mode: str, *, symbol: str, reason: str) -> None:
        from_mode = self._mode
        if from_mode == to_mode:
            return
        self._mode = to_mode
        self.datastore.set_mode(to_mode)
        logging.warning("source_mode_switch %s -> %s | reason=%s | symbol=%s", from_mode, to_mode, reason, symbol)

    def _log_health_if_needed(self, now_mono: float, now: datetime) -> None:
        if now_mono - self._last_health_log < 60:
            return
        self._last_health_log = now_mono

        for symbol in self.symbols:
            snap = self.datastore.snapshot(symbol)
            price_age = self._age_seconds(now, snap.last_price_ts)
            kline_age = self._age_seconds(now, snap.last_kline_close_ts)
            price_size, kline_size = self.datastore.buffer_sizes(symbol)
            logging.info(
                "health mode=%s symbol=%s last_price_age_seconds=%s last_kline_age_seconds=%s price_buffer=%d kline_buffer=%d",
                self._mode,
                symbol,
                "na" if price_age is None else f"{price_age:.1f}",
                "na" if kline_age is None else f"{kline_age:.1f}",
                price_size,
                kline_size,
            )

    @staticmethod
    def _age_seconds(now: datetime, ts: datetime | None) -> float | None:
        if ts is None:
            return None
        return (now - ts).total_seconds()
