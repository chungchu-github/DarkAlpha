from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from typing import Protocol

from dark_alpha_phase_one.data.datastore import DataStore


class RestClientProtocol(Protocol):
    def fetch_price(self, symbol: str): ...

    def fetch_klines(self, symbol: str, limit: int): ...

    def fetch_premium_index(self, symbol: str): ...

    def fetch_funding_rate_history(self, symbol: str, limit: int = 3): ...

    def fetch_open_interest(self, symbol: str): ...

    def fetch_server_time_ms(self) -> int: ...


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
        premiumindex_poll_seconds: float,
        funding_poll_seconds: float,
        oi_poll_seconds: float,
        funding_history_limit: int = 3,
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
        self.premiumindex_poll_seconds = premiumindex_poll_seconds
        self.funding_poll_seconds = funding_poll_seconds
        self.oi_poll_seconds = oi_poll_seconds
        self.funding_history_limit = funding_history_limit

        self._mode = "ws" if preferred_mode == "ws" else "rest"
        self._ws_good_ticks = 0
        self._last_rest_price_poll = 0.0
        self._last_rest_kline_poll = 0.0
        self._last_premium_poll = 0.0
        self._last_funding_poll = 0.0
        self._last_oi_poll = 0.0
        self._last_health_log = 0.0
        self._ws_backoff = ws_backoff_min
        self._ws_next_retry_at = 0.0
        self._clock_skew_ms = 0

        self.datastore.set_mode(self._mode)
        self._init_clock_skew()
        self._safe_state_sync(reason="bootstrap")
        if self._mode == "ws":
            self._connect_ws(initial=True)

    def _init_clock_skew(self) -> None:
        local_ms = int(time.time() * 1000)
        try:
            server_ms = self.rest_client.fetch_server_time_ms()
            self._clock_skew_ms = server_ms - local_ms
            logging.info(
                "Clock sync established server_ms=%d local_ms=%d clock_skew_ms=%d",
                server_ms,
                local_ms,
                self._clock_skew_ms,
            )
        except Exception as exc:  # noqa: BLE001
            self._clock_skew_ms = 0
            logging.warning("Clock sync failed, using local clock only: %s", exc)

    def _corrected_now(self) -> datetime:
        corrected_ms = int(time.time() * 1000) + self._clock_skew_ms
        return datetime.fromtimestamp(corrected_ms / 1000, tz=timezone.utc)

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
        now = self._corrected_now()
        now_mono = time.monotonic()

        self._attempt_ws_events()
        self._evaluate_staleness(now)
        self._poll_derivatives(now_mono)

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
        now = self._corrected_now()
        for tick in ticks:
            self.datastore.update_price(tick.symbol, tick.price, tick.ts)
            age = self._age_seconds(now, tick.ts)
            if age is not None and age <= self.stale_seconds:
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

    def _poll_derivatives(self, now_mono: float) -> None:
        if now_mono - self._last_premium_poll >= self.premiumindex_poll_seconds:
            for symbol in self.symbols:
                try:
                    mark, funding_rate, next_funding_ms, ts = self.rest_client.fetch_premium_index(symbol)
                    self.datastore.update_premium_index(
                        symbol,
                        mark_price=mark,
                        last_funding_rate=funding_rate,
                        next_funding_time_ms=next_funding_ms,
                        ts=ts,
                    )
                except Exception as exc:  # noqa: BLE001
                    logging.warning("premiumIndex poll failed for %s: %s", symbol, exc)
            self._last_premium_poll = now_mono

        if now_mono - self._last_funding_poll >= self.funding_poll_seconds:
            for symbol in self.symbols:
                try:
                    history, ts = self.rest_client.fetch_funding_rate_history(
                        symbol,
                        limit=self.funding_history_limit,
                    )
                    self.datastore.update_funding_rate_history(symbol, history, ts)
                except Exception as exc:  # noqa: BLE001
                    logging.warning("fundingRate poll failed for %s: %s", symbol, exc)
            self._last_funding_poll = now_mono

        if now_mono - self._last_oi_poll >= self.oi_poll_seconds:
            for symbol in self.symbols:
                try:
                    oi, ts = self.rest_client.fetch_open_interest(symbol)
                    self.datastore.update_open_interest(symbol, oi, ts)
                except Exception as exc:  # noqa: BLE001
                    logging.warning("openInterest poll failed for %s: %s", symbol, exc)
            self._last_oi_poll = now_mono

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

        now_ms_corrected = int(now.timestamp() * 1000)
        for symbol in self.symbols:
            snap = self.datastore.snapshot(symbol)
            price_age = self._age_seconds(now, snap.last_price_ts)
            kline_age = self._age_seconds(now, snap.last_kline_close_ts)
            funding_age = self._age_seconds(now, snap.funding_ts)
            oi_age = self._age_seconds(now, snap.open_interest_ts)
            price_size, kline_size = self.datastore.buffer_sizes(symbol)
            logging.info(
                "health mode=%s symbol=%s now_ms_corrected=%d clock_skew_ms=%d "
                "last_price_ts_ms=%s last_kline_close_ts_ms=%s funding_ts_ms=%s open_interest_ts_ms=%s "
                "last_price_age_seconds=%s last_kline_age_seconds=%s funding_age_seconds=%s oi_age_seconds=%s "
                "price_buffer=%d kline_buffer=%d",
                self._mode,
                symbol,
                now_ms_corrected,
                self._clock_skew_ms,
                self._to_ms(snap.last_price_ts),
                self._to_ms(snap.last_kline_close_ts),
                self._to_ms(snap.funding_ts),
                self._to_ms(snap.open_interest_ts),
                "na" if price_age is None else f"{price_age:.1f}",
                "na" if kline_age is None else f"{kline_age:.1f}",
                "na" if funding_age is None else f"{funding_age:.1f}",
                "na" if oi_age is None else f"{oi_age:.1f}",
                price_size,
                kline_size,
            )

    @staticmethod
    def _to_ms(ts: datetime | None) -> str:
        if ts is None:
            return "na"
        return str(int(ts.timestamp() * 1000))

    @staticmethod
    def _age_seconds(now: datetime, ts: datetime | None) -> float | None:
        if ts is None:
            return None
        age = (now - ts).total_seconds()
        if age < 0:
            if age > -0.5:
                logging.debug("Clamping tiny negative age_seconds %.3f to 0", age)
            else:
                logging.debug("Clamping negative age_seconds %.3f to 0 (possible clock skew)", age)
            return 0.0
        return age
