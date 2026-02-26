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
        max_clock_error_ms: int = 1000,
        kline_stale_ms: int | None = None,
    ) -> None:
        self.symbols = symbols
        self.datastore = datastore
        self.rest_client = rest_client
        self.ws_client = ws_client
        self.preferred_mode = preferred_mode
        self.stale_seconds = stale_seconds
        self.kline_stale_ms = kline_stale_ms if kline_stale_ms is not None else kline_stale_seconds * 1000
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
        self.max_clock_error_ms = max_clock_error_ms

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
        self._last_server_ms: int | None = None

        self.datastore.set_mode(self._mode)
        self._init_clock_skew()
        self._safe_state_sync(reason="bootstrap")
        if self._mode == "ws":
            self._connect_ws(initial=True)

    @staticmethod
    def compute_clock_skew_ms(*, local_ms: int, server_ms: int) -> int:
        return server_ms - local_ms

    @staticmethod
    def compute_now_ms_corrected(*, local_ms: int, clock_skew_ms: int) -> int:
        return local_ms + clock_skew_ms

    @staticmethod
    def dt_to_ms(ts: datetime | None) -> int | None:
        if ts is None:
            return None
        return int(ts.timestamp() * 1000)

    @staticmethod
    def raw_age_ms(now_ms: int, ts_ms: int | None) -> int | None:
        if ts_ms is None:
            return None
        return now_ms - ts_ms

    @staticmethod
    def age_seconds_from_raw(raw_age_ms: int | None) -> float | None:
        if raw_age_ms is None:
            return None
        if raw_age_ms < 0:
            return 0.0
        return raw_age_ms / 1000.0

    def now_ms_corrected(self) -> int:
        local_ms = int(time.time() * 1000)
        now_ms = self.compute_now_ms_corrected(local_ms=local_ms, clock_skew_ms=self._clock_skew_ms)
        if self._last_server_ms is not None:
            drift_ms = abs(now_ms - self._last_server_ms)
            if drift_ms > self.max_clock_error_ms:
                logging.warning(
                    "clock_sanity_fallback unit=ms local_ms=%d server_ms=%d skew_ms=%d now_ms_corrected=%d drift_ms=%d max_clock_error_ms=%d",
                    local_ms,
                    self._last_server_ms,
                    self._clock_skew_ms,
                    now_ms,
                    drift_ms,
                    self.max_clock_error_ms,
                )
                return self._last_server_ms
        return now_ms

    def _now_dt_corrected(self) -> datetime:
        return datetime.fromtimestamp(self.now_ms_corrected() / 1000, tz=timezone.utc)

    def _init_clock_skew(self) -> None:
        local_ms = int(time.time() * 1000)
        try:
            server_ms = self.rest_client.fetch_server_time_ms()
            skew_ms = self.compute_clock_skew_ms(local_ms=local_ms, server_ms=server_ms)
            now_ms = self.compute_now_ms_corrected(local_ms=local_ms, clock_skew_ms=skew_ms)
            self._clock_skew_ms = skew_ms
            self._last_server_ms = server_ms
            logging.info(
                "clock_sync unit=ms local_ms=%d server_ms=%d skew_ms=%d now_ms_corrected=%d",
                local_ms,
                server_ms,
                skew_ms,
                now_ms,
            )
            if abs(now_ms - server_ms) > self.max_clock_error_ms:
                logging.warning(
                    "clock_sync_sanity_failed unit=ms local_ms=%d server_ms=%d skew_ms=%d now_ms_corrected=%d max_clock_error_ms=%d",
                    local_ms,
                    server_ms,
                    skew_ms,
                    now_ms,
                    self.max_clock_error_ms,
                )
                self._clock_skew_ms = self.compute_clock_skew_ms(local_ms=local_ms, server_ms=server_ms)
                self._last_server_ms = server_ms
        except Exception as exc:  # noqa: BLE001
            self._clock_skew_ms = 0
            self._last_server_ms = None
            logging.warning("Clock sync failed, using local clock only: %s", exc)

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
        now_mono = time.monotonic()

        self._attempt_ws_events()
        self._evaluate_staleness()
        self._poll_derivatives(now_mono)

        if self._mode == "rest":
            self._poll_rest_prices(now_mono)
            self._poll_rest_klines(now_mono)
            self._attempt_ws_recover(now_mono)

        self._log_health_if_needed(now_mono)

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
        now_dt = self._now_dt_corrected()
        now_ms = self.now_ms_corrected()
        for tick in ticks:
            self.datastore.update_price(tick.symbol, tick.price, now_dt)
            raw_age = self.raw_age_ms(now_ms, self.dt_to_ms(now_dt))
            if raw_age is not None and raw_age <= self.stale_seconds * 1000:
                fresh_ticks += 1
        for kline_tick in kline_ticks:
            self.datastore.upsert_ws_kline(
                kline_tick.symbol,
                kline_tick.candle,
                kline_tick.open_time_ms,
                kline_tick.is_closed,
                now_dt,
            )
        return fresh_ticks

    def _poll_derivatives(self, now_mono: float) -> None:
        if now_mono - self._last_premium_poll >= self.premiumindex_poll_seconds:
            for symbol in self.symbols:
                try:
                    mark, funding_rate, next_funding_ms, _ = self.rest_client.fetch_premium_index(symbol)
                    self.datastore.update_premium_index(
                        symbol,
                        mark_price=mark,
                        last_funding_rate=funding_rate,
                        next_funding_time_ms=next_funding_ms,
                        ts=self._now_dt_corrected(),
                    )
                except Exception as exc:  # noqa: BLE001
                    logging.warning("premiumIndex poll failed for %s: %s", symbol, exc)
            self._last_premium_poll = now_mono

        if now_mono - self._last_funding_poll >= self.funding_poll_seconds:
            for symbol in self.symbols:
                try:
                    history, _ = self.rest_client.fetch_funding_rate_history(
                        symbol,
                        limit=self.funding_history_limit,
                    )
                    self.datastore.update_funding_rate_history(symbol, history, self._now_dt_corrected())
                except Exception as exc:  # noqa: BLE001
                    logging.warning("fundingRate poll failed for %s: %s", symbol, exc)
            self._last_funding_poll = now_mono

        if now_mono - self._last_oi_poll >= self.oi_poll_seconds:
            for symbol in self.symbols:
                try:
                    oi, _ = self.rest_client.fetch_open_interest(symbol)
                    self.datastore.update_open_interest(symbol, oi, self._now_dt_corrected())
                except Exception as exc:  # noqa: BLE001
                    logging.warning("openInterest poll failed for %s: %s", symbol, exc)
            self._last_oi_poll = now_mono

    def _poll_rest_prices(self, now_mono: float) -> None:
        if now_mono - self._last_rest_price_poll < self.rest_price_poll_seconds:
            return
        for symbol in self.symbols:
            price, _ = self.rest_client.fetch_price(symbol)
            self.datastore.update_price(symbol, price, self._now_dt_corrected())
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
        now_dt = self._now_dt_corrected()
        for symbol in self.symbols:
            klines, _ = self.rest_client.fetch_klines(symbol, limit=limit)
            self.datastore.merge_klines(symbol, klines, now_dt)
            logging.info("State sync (%s) for %s with %d klines", reason, symbol, len(klines))

    def _evaluate_staleness(self) -> None:
        if self._mode != "ws":
            return

        now_ms = self.now_ms_corrected()
        for symbol in self.symbols:
            snap = self.datastore.snapshot(symbol)
            price_age_raw = self.raw_age_ms(now_ms, self.dt_to_ms(snap.last_price_ts))
            kline_recv_raw = self.raw_age_ms(now_ms, self.dt_to_ms(snap.last_kline_recv_ts))
            if price_age_raw is not None and price_age_raw > self.stale_seconds * 1000:
                self._switch_mode("rest", symbol=symbol, reason="stale")
                return
            if kline_recv_raw is not None and kline_recv_raw > self.kline_stale_ms:
                logging.warning(
                    "kline_stale_switch unit=ms symbol=%s now_ms_corrected=%d last_kline_recv_ms=%s raw_age_ms=%d threshold_ms=%d",
                    symbol,
                    now_ms,
                    self.dt_to_ms(snap.last_kline_recv_ts),
                    kline_recv_raw,
                    self.kline_stale_ms,
                )
                self._switch_mode("rest", symbol=symbol, reason="kline_stale")
                return

    def _switch_mode(self, to_mode: str, *, symbol: str, reason: str) -> None:
        from_mode = self._mode
        if from_mode == to_mode:
            return
        self._mode = to_mode
        self.datastore.set_mode(to_mode)
        logging.warning("source_mode_switch %s -> %s | reason=%s | symbol=%s", from_mode, to_mode, reason, symbol)

    def _log_health_if_needed(self, now_mono: float) -> None:
        if now_mono - self._last_health_log < 60:
            return
        self._last_health_log = now_mono

        now_ms = self.now_ms_corrected()
        for symbol in self.symbols:
            snap = self.datastore.snapshot(symbol)
            fields = {
                "last_price": self.dt_to_ms(snap.last_price_ts),
                "last_kline_close": self.dt_to_ms(snap.last_kline_close_ts),
                "last_kline_recv": self.dt_to_ms(snap.last_kline_recv_ts),
                "funding": self.dt_to_ms(snap.funding_ts),
                "open_interest": self.dt_to_ms(snap.open_interest_ts),
            }
            raws = {name: self.raw_age_ms(now_ms, ts_ms) for name, ts_ms in fields.items()}
            for field_name, raw_age in raws.items():
                if raw_age is not None and raw_age < 0:
                    logging.warning(
                        "timestamp_in_future unit=ms symbol=%s field=%s ahead_ms=%d now_ms_corrected=%d ts_ms=%s",
                        symbol,
                        field_name,
                        abs(raw_age),
                        now_ms,
                        fields[field_name],
                    )

            price_size, kline_size = self.datastore.buffer_sizes(symbol)
            logging.info(
                "health mode=%s symbol=%s now_ms_corrected=%d clock_skew_ms=%d "
                "last_price_ts_ms=%s last_kline_close_ts_ms=%s last_kline_recv_ts_ms=%s funding_ts_ms=%s open_interest_ts_ms=%s "
                "last_price_raw_age_ms=%s last_kline_close_raw_age_ms=%s last_kline_recv_raw_age_ms=%s funding_raw_age_ms=%s oi_raw_age_ms=%s "
                "last_price_age_seconds=%s last_kline_age_seconds=%s last_kline_recv_age_seconds=%s funding_age_seconds=%s oi_age_seconds=%s "
                "price_buffer=%d kline_buffer=%d",
                self._mode,
                symbol,
                now_ms,
                self._clock_skew_ms,
                self._fmt_int(fields["last_price"]),
                self._fmt_int(fields["last_kline_close"]),
                self._fmt_int(fields["last_kline_recv"]),
                self._fmt_int(fields["funding"]),
                self._fmt_int(fields["open_interest"]),
                self._fmt_int(raws["last_price"]),
                self._fmt_int(raws["last_kline_close"]),
                self._fmt_int(raws["last_kline_recv"]),
                self._fmt_int(raws["funding"]),
                self._fmt_int(raws["open_interest"]),
                self._fmt_float(self.age_seconds_from_raw(raws["last_price"])),
                self._fmt_float(self.age_seconds_from_raw(raws["last_kline_close"])),
                self._fmt_float(self.age_seconds_from_raw(raws["last_kline_recv"])),
                self._fmt_float(self.age_seconds_from_raw(raws["funding"])),
                self._fmt_float(self.age_seconds_from_raw(raws["open_interest"])),
                price_size,
                kline_size,
            )

    @staticmethod
    def _fmt_float(value: float | None) -> str:
        if value is None:
            return "na"
        return f"{value:.1f}"

    @staticmethod
    def _fmt_int(value: int | None) -> str:
        if value is None:
            return "na"
        return str(value)
