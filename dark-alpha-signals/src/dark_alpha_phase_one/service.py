from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import logging
import time

from .calculations import (
    Candle,
    aggregate_klines_to_window,
    aggregate_oi_to_15m,
    atr_series,
    calculate_return,
    compute_oi_delta_pct_15m,
    compute_oi_zscore_15m,
    calculate_position_usdt,
)
from .config import Settings
from .data.binance_rest import BinanceRestDataClient
from .data.binance_ws import BinanceWsClient
from .data.datastore import DataStore, SymbolSnapshot
from .data.source_manager import SourceManager
from .engine.arbitrator import Arbitrator, ArbitratorConfig
from .engine.signal_context import SignalContext
from .models import ProposalCard
from .risk_engine import RiskEngine
from .postback_client import PostbackClient
from .strategies.base import Strategy
from .strategies.fake_breakout_reversal import FakeBreakoutReversalStrategy
from .strategies.funding_oi_skew import FundingOiSkewStrategy
from .strategies.liquidation_follow import LiquidationFollowStrategy
from .strategies.vol_breakout import VolBreakoutStrategy
from .telegram_client import TelegramNotifier

ATR_PERIOD_15M = 14
ATR_WINDOW_MINUTES = 15
MIN_1M_BARS_FOR_ATR = ATR_WINDOW_MINUTES * ATR_PERIOD_15M


def should_emit_test(*, last_emit_ts: float | None, now_ts: float, interval_sec: int) -> bool:
    if last_emit_ts is None:
        return True
    return now_ts - last_emit_ts >= interval_sec



def build_signal_context(
    *,
    symbol: str,
    price: float,
    klines_1m: list[Candle],
    funding_rate: float,
    open_interest: float,
    mark_price: float,
    open_interest_series: list[tuple[datetime, float]],
    last_kline_close_ts: datetime | None,
) -> SignalContext | None:
    closes = [candle.close for candle in klines_1m]
    return_5m = calculate_return(closes, lookback_minutes=5)
    candles_15m = aggregate_klines_to_window(klines_1m, window=ATR_WINDOW_MINUTES)
    atr_values = atr_series(candles_15m, period=ATR_PERIOD_15M)

    if not atr_values:
        return None

    atr_15m = atr_values[-1]
    baseline_window = min(96, len(atr_values) - 1)
    if baseline_window <= 0:
        atr_baseline = atr_15m
    else:
        atr_baseline = sum(atr_values[-(baseline_window + 1) : -1]) / baseline_window

    oi_15m_windows = aggregate_oi_to_15m(open_interest_series)
    oi_zscore = compute_oi_zscore_15m(oi_15m_windows, baseline_windows=96)
    oi_delta = compute_oi_delta_pct_15m(oi_15m_windows)

    return SignalContext(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        price=price,
        klines_1m=klines_1m,
        return_5m=return_5m,
        atr_15m=atr_15m,
        atr_15m_baseline=atr_baseline,
        funding_rate=funding_rate,
        open_interest=open_interest,
        mark_price=mark_price,
        open_interest_zscore_15m=oi_zscore,
        open_interest_delta_15m=oi_delta,
        last_kline_close_ts=last_kline_close_ts,
    )




@dataclass(frozen=True)
class DerivativesGate:
    allow: bool
    oi_status: str
    funding_raw_age_ms: int | None
    oi_raw_age_ms: int | None
    reason: str

def derivatives_are_fresh(
    snapshot: SymbolSnapshot,
    *,
    now_ms_corrected: int,
    funding_stale_ms: int,
    oi_stale_ms: int,
) -> DerivativesGate:
    funding_ts_ms = SourceManager.dt_to_ms(snapshot.funding_ts)
    oi_ts_ms = SourceManager.dt_to_ms(snapshot.open_interest_ts)
    funding_raw_age_ms = SourceManager.raw_age_ms(now_ms_corrected, funding_ts_ms)
    oi_raw_age_ms = SourceManager.raw_age_ms(now_ms_corrected, oi_ts_ms)

    if funding_raw_age_ms is None:
        logging.info(
            "derivatives_stale_check unit=ms symbol=%s mode=%s now_ms_corrected=%d funding_raw_age_ms=na funding_threshold_ms=%d oi_raw_age_ms=%s oi_threshold_ms=%d oi_status=unknown skip=true reason=funding_missing",
            snapshot.symbol,
            snapshot.data_source_mode,
            now_ms_corrected,
            funding_stale_ms,
            "na" if oi_raw_age_ms is None else str(oi_raw_age_ms),
            oi_stale_ms,
        )
        return DerivativesGate(
            allow=False,
            oi_status="unknown",
            funding_raw_age_ms=None,
            oi_raw_age_ms=oi_raw_age_ms,
            reason="funding_missing",
        )

    funding_stale = funding_raw_age_ms > funding_stale_ms
    oi_status = "unknown"
    if oi_raw_age_ms is not None:
        oi_status = "stale" if oi_raw_age_ms > oi_stale_ms else "fresh"

    skip = funding_stale
    reason = "funding_stale" if funding_stale else "ok"

    logging.info(
        "derivatives_stale_check unit=ms symbol=%s mode=%s now_ms_corrected=%d funding_raw_age_ms=%d funding_threshold_ms=%d oi_raw_age_ms=%s oi_threshold_ms=%d oi_status=%s skip=%s reason=%s",
        snapshot.symbol,
        snapshot.data_source_mode,
        now_ms_corrected,
        funding_raw_age_ms,
        funding_stale_ms,
        "na" if oi_raw_age_ms is None else str(oi_raw_age_ms),
        oi_stale_ms,
        oi_status,
        str(skip).lower(),
        reason,
    )

    return DerivativesGate(
        allow=not skip,
        oi_status=oi_status,
        funding_raw_age_ms=funding_raw_age_ms,
        oi_raw_age_ms=oi_raw_age_ms,
        reason=reason,
    )



class SignalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.run_id = uuid4().hex
        self._atr_warmup_logged_symbols: set[str] = set()
        self._last_test_emit_ts_by_symbol: dict[str, float] = {}
        self.datastore = DataStore(symbols=settings.symbols)
        self.source_manager = SourceManager(
            symbols=settings.symbols,
            datastore=self.datastore,
            rest_client=BinanceRestDataClient(),
            ws_client=BinanceWsClient(settings.symbols),
            preferred_mode=settings.data_source_preferred,
            stale_seconds=settings.stale_seconds,
            kline_stale_seconds=settings.kline_stale_seconds,
            ws_backoff_min=settings.ws_backoff_min,
            ws_backoff_max=settings.ws_backoff_max,
            rest_price_poll_seconds=settings.rest_price_poll_seconds,
            rest_kline_poll_seconds=settings.rest_kline_poll_seconds,
            ws_recover_good_ticks=settings.ws_recover_good_ticks,
            state_sync_klines=settings.state_sync_klines,
            premiumindex_poll_seconds=settings.premiumindex_poll_seconds,
            funding_poll_seconds=settings.funding_poll_seconds,
            oi_poll_seconds=settings.oi_poll_seconds,
            max_clock_error_ms=settings.max_clock_error_ms,
            kline_stale_ms=settings.kline_stale_ms,
            server_time_refresh_sec=settings.server_time_refresh_sec,
            server_time_degraded_retry_sec=settings.server_time_degraded_retry_sec,
            clock_refresh_cooldown_ms=settings.clock_refresh_cooldown_ms,
            clock_degraded_ttl_ms=settings.clock_degraded_ttl_ms,
        )
        self.telegram_notifier = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
        self.postback_client = PostbackClient(settings.postback_url)
        self.risk_engine = RiskEngine(
            state_path=settings.risk_state_path,
            max_daily_loss_usdt=settings.max_daily_loss_usdt,
            max_cards_per_day=settings.max_cards_per_day,
            cooldown_after_trigger_minutes=settings.cooldown_after_trigger_minutes,
            kill_switch=settings.kill_switch,
            pnl_csv_path=settings.pnl_csv_path,
        )
        self.arbitrator = Arbitrator(
            ArbitratorConfig(
                dedupe_window_seconds=settings.dedupe_window_seconds,
                entry_similar_pct=settings.entry_similar_pct,
                stop_similar_pct=settings.stop_similar_pct,
            ),
            last_sent_lookup=self.risk_engine.get_last_trigger_time,
        )
        self.strategies: list[Strategy] = [
            FakeBreakoutReversalStrategy(
                sweep_pct=settings.sweep_pct,
                wick_body_ratio=settings.wick_body_ratio,
                stop_buffer_atr=settings.stop_buffer_atr,
                min_atr_pct=settings.min_atr_pct,
                leverage_suggest=50,
                max_risk_usdt=settings.max_risk_usdt,
                ttl_minutes=5,
                priority=settings.priority_fake_breakout,
            ),
            FundingOiSkewStrategy(
                funding_extreme=settings.funding_extreme,
                oi_zscore_threshold=settings.oi_zscore,
                leverage_suggest=35,
                max_risk_usdt=settings.max_risk_usdt,
                ttl_minutes=12,
                priority=settings.priority_funding_oi_skew,
            ),
            LiquidationFollowStrategy(
                oi_delta_pct_threshold=settings.oi_delta_pct,
                leverage_suggest=30,
                max_risk_usdt=settings.max_risk_usdt,
                ttl_minutes=10,
                priority=settings.priority_liquidation_follow,
            ),
            VolBreakoutStrategy(
                return_threshold=settings.return_threshold,
                atr_spike_multiplier=settings.atr_spike_multiplier,
                leverage_suggest=settings.leverage_suggest,
                max_risk_usdt=settings.max_risk_usdt,
                ttl_minutes=settings.ttl_minutes,
                priority=settings.priority_vol_breakout,
            ),
        ]


    def _cooldown_remaining_ms(self, symbol: str) -> int:
        last_trigger = self.risk_engine.get_last_trigger_time(symbol)
        if last_trigger is None:
            return 0
        cooldown_until = last_trigger + timedelta(minutes=self.settings.cooldown_after_trigger_minutes)
        now = datetime.now(tz=timezone.utc)
        if now >= cooldown_until:
            return 0
        return int((cooldown_until - now).total_seconds() * 1000)

    def _log_signal_decision(
        self,
        *,
        symbol: str,
        decision: str,
        reason: str,
        trace_id: str | None,
        atr: float | None,
        trend_score: float | None,
        price_dist_pct: float | None,
        derivatives_ok: bool,
    ) -> None:
        logging.info(
            "event=signal_decision run_id=%s symbol=%s tf=%s decision=%s reason=%s cooldown_remaining_ms=%d atr=%s trend_score=%s price_dist_pct=%s derivatives_ok=%s trace_id=%s",
            self.run_id,
            symbol,
            self.settings.test_emit_tf,
            decision,
            reason,
            self._cooldown_remaining_ms(symbol),
            "na" if atr is None else f"{atr:.6f}",
            "na" if trend_score is None else f"{trend_score:.6f}",
            "na" if price_dist_pct is None else f"{price_dist_pct:.6f}",
            str(derivatives_ok).lower(),
            trace_id or "na",
        )


    def _build_test_emit_card(self, symbol: str, snapshot: SymbolSnapshot) -> ProposalCard | None:
        if snapshot.price is None:
            return None
        entry = snapshot.price
        stop = entry * 0.998
        try:
            position_usdt = calculate_position_usdt(entry=entry, stop=stop, max_risk_usdt=self.settings.max_risk_usdt)
        except ValueError:
            return None
        return ProposalCard.create(
            symbol=symbol,
            strategy="test_emit_dryrun",
            side="LONG",
            entry=entry,
            stop=stop,
            leverage_suggest=self.settings.leverage_suggest,
            position_usdt=position_usdt,
            max_risk_usdt=self.settings.max_risk_usdt,
            ttl_minutes=5,
            rationale="TEST DRYRUN emit for pipeline verification",
            priority=10_000,
            confidence=100.0,
            oi_status="fresh",
        )

    def _maybe_test_emit(self, symbol: str, snapshot: SymbolSnapshot, derivatives_gate: DerivativesGate) -> tuple[ProposalCard | None, str | None]:
        if not self.settings.test_emit_enabled:
            return None, None
        if symbol not in self.settings.test_emit_symbols:
            return None, None
        now_ts = time.time()
        last_emit_ts = self._last_test_emit_ts_by_symbol.get(symbol)
        if not should_emit_test(last_emit_ts=last_emit_ts, now_ts=now_ts, interval_sec=self.settings.test_emit_interval_sec):
            return None, None

        card = self._build_test_emit_card(symbol, snapshot)
        if card is None:
            return None, None

        trace_id = uuid4().hex
        self._last_test_emit_ts_by_symbol[symbol] = now_ts
        self._log_signal_decision(
            symbol=symbol,
            decision="emit",
            reason="test_emit",
            trace_id=trace_id,
            atr=None,
            trend_score=None,
            price_dist_pct=None,
            derivatives_ok=derivatives_gate.allow,
        )
        return replace(card, oi_status=derivatives_gate.oi_status), trace_id

    def evaluate_symbol(self, symbol: str) -> tuple[ProposalCard | None, str | None]:
        snapshot = self.datastore.snapshot(symbol)
        if snapshot.price is None or not snapshot.klines_1m:
            logging.debug("Data not ready for %s in mode=%s", symbol, snapshot.data_source_mode)
            self._log_signal_decision(symbol=symbol, decision="no_signal", reason="data_not_ready", trace_id=None, atr=None, trend_score=None, price_dist_pct=None, derivatives_ok=False)
            return None, None

        now_ms_corrected = self.source_manager.now_ms_corrected()
        derivatives_gate = derivatives_are_fresh(
            snapshot,
            now_ms_corrected=now_ms_corrected,
            funding_stale_ms=self.settings.funding_stale_ms,
            oi_stale_ms=self.settings.oi_stale_ms,
        )
        if not derivatives_gate.allow:
            logging.info(
                "Derivatives stale for %s, skip card generation reason=%s funding_raw_age_ms=%s oi_raw_age_ms=%s oi_status=%s",
                symbol,
                derivatives_gate.reason,
                derivatives_gate.funding_raw_age_ms,
                derivatives_gate.oi_raw_age_ms,
                derivatives_gate.oi_status,
            )
            stale_reason = "data_stale" if derivatives_gate.reason == "funding_missing" else "derivatives_stale"
            self._log_signal_decision(symbol=symbol, decision="blocked", reason=stale_reason, trace_id=None, atr=None, trend_score=None, price_dist_pct=None, derivatives_ok=False)
            return None, None

        if snapshot.last_funding_rate is None or snapshot.open_interest is None or snapshot.mark_price is None:
            logging.info("Derivatives missing for %s, skip card generation", symbol)
            self._log_signal_decision(symbol=symbol, decision="blocked", reason="derivatives_missing", trace_id=None, atr=None, trend_score=None, price_dist_pct=None, derivatives_ok=False)
            return None, None

        atr_need_bars_1m = MIN_1M_BARS_FOR_ATR
        if len(snapshot.klines_1m) < atr_need_bars_1m:
            if symbol not in self._atr_warmup_logged_symbols:
                logging.info(
                    "ATR warmup for %s: have_1m_bars=%d need_1m_bars=%d period_15m=%d timeframe=1m->15m",
                    symbol,
                    len(snapshot.klines_1m),
                    atr_need_bars_1m,
                    ATR_PERIOD_15M,
                )
                self._atr_warmup_logged_symbols.add(symbol)
            test_card, test_trace = self._maybe_test_emit(symbol, snapshot, derivatives_gate)
            if test_card is not None:
                return test_card, test_trace
            self._log_signal_decision(symbol=symbol, decision="no_signal", reason="atr_warmup", trace_id=None, atr=None, trend_score=None, price_dist_pct=None, derivatives_ok=True)
            return None, None

        signal_context = build_signal_context(
            symbol=symbol,
            price=snapshot.price,
            klines_1m=snapshot.klines_1m,
            funding_rate=snapshot.last_funding_rate,
            open_interest=snapshot.open_interest,
            mark_price=snapshot.mark_price,
            open_interest_series=snapshot.open_interest_series,
            last_kline_close_ts=snapshot.last_kline_close_ts,
        )
        if signal_context is None:
            logging.warning(
                "Not enough data to compute ATR for %s (have_1m_bars=%d need_1m_bars=%d period_15m=%d timeframe=1m->15m)",
                symbol,
                len(snapshot.klines_1m),
                atr_need_bars_1m,
                ATR_PERIOD_15M,
            )
            test_card, test_trace = self._maybe_test_emit(symbol, snapshot, derivatives_gate)
            if test_card is not None:
                return test_card, test_trace
            self._log_signal_decision(symbol=symbol, decision="no_signal", reason="atr_unavailable", trace_id=None, atr=None, trend_score=None, price_dist_pct=None, derivatives_ok=True)
            return None, None

        self._atr_warmup_logged_symbols.discard(symbol)

        candidates = self._collect_strategy_cards(signal_context)
        card = self.arbitrator.choose_best(candidates, signal_context)
        if card is None:
            test_card, test_trace = self._maybe_test_emit(symbol, snapshot, derivatives_gate)
            if test_card is not None:
                return test_card, test_trace
            self._log_signal_decision(symbol=symbol, decision="no_signal", reason="strategy_no_card", trace_id=None, atr=signal_context.atr_15m, trend_score=signal_context.return_5m, price_dist_pct=abs(signal_context.price - signal_context.mark_price) / signal_context.price if signal_context.price else None, derivatives_ok=True)
            return None, None

        risk_decision = self.risk_engine.evaluate(symbol)
        if not risk_decision.allowed:
            logging.info("Risk blocked %s: %s", symbol, risk_decision.reason)
            test_card, test_trace = self._maybe_test_emit(symbol, snapshot, derivatives_gate)
            if test_card is not None:
                return test_card, test_trace
            self._log_signal_decision(symbol=symbol, decision="blocked", reason=risk_decision.reason, trace_id=None, atr=signal_context.atr_15m, trend_score=signal_context.return_5m, price_dist_pct=abs(signal_context.price - signal_context.mark_price) / signal_context.price if signal_context.price else None, derivatives_ok=True)
            return None, None

        trace_id = uuid4().hex
        self._log_signal_decision(symbol=symbol, decision="emit", reason="ok", trace_id=trace_id, atr=signal_context.atr_15m, trend_score=signal_context.return_5m, price_dist_pct=abs(signal_context.price - signal_context.mark_price) / signal_context.price if signal_context.price else None, derivatives_ok=True)
        self.risk_engine.record_trigger(symbol)
        return replace(card, oi_status=derivatives_gate.oi_status), trace_id

    def _collect_strategy_cards(self, signal_context: SignalContext) -> list[ProposalCard]:
        cards: list[ProposalCard] = []
        for strategy in self.strategies:
            card = strategy.generate(signal_context)
            if card is not None:
                cards.append(card)
        return cards

    def run_forever(self) -> None:
        logging.info("Starting signal service for symbols: %s", self.settings.symbols)
        while True:
            try:
                self.source_manager.refresh()
                for symbol in self.settings.symbols:
                    card, trace_id = self.evaluate_symbol(symbol)
                    if card is None or trace_id is None:
                        continue

                    started = time.perf_counter()
                    payload = card.to_dict()
                    payload["trace_id"] = trace_id
                    logging.info("event=card_build_start run_id=%s trace_id=%s symbol=%s tf=%s", self.run_id, trace_id, symbol, self.settings.test_emit_tf)
                    build_start = time.perf_counter()
                    try:
                        payload_size = len(str(payload).encode("utf-8"))
                        render_ms = int((time.perf_counter() - build_start) * 1000)
                        logging.info("event=card_build_success run_id=%s trace_id=%s symbol=%s tf=%s bytes=%d render_ms=%d", self.run_id, trace_id, symbol, self.settings.test_emit_tf, payload_size, render_ms)
                    except Exception as exc:  # noqa: BLE001
                        logging.error("event=card_build_fail run_id=%s trace_id=%s symbol=%s tf=%s err=%s", self.run_id, trace_id, symbol, self.settings.test_emit_tf, exc)
                        logging.info("event=emit_pipeline_result run_id=%s trace_id=%s symbol=%s tf=%s result=fail card=false telegram=not_sent postback=not_sent total_ms=%d", self.run_id, trace_id, symbol, self.settings.test_emit_tf, int((time.perf_counter() - started) * 1000))
                        continue

                    telegram_status = "not_sent"
                    postback_status = "not_sent"

                    logging.info("event=telegram_send_start run_id=%s trace_id=%s symbol=%s tf=%s attempt=1", self.run_id, trace_id, symbol, self.settings.test_emit_tf)
                    tg_ok, tg_http, tg_message_id, tg_latency = self.telegram_notifier.send_json_card(payload)
                    if tg_ok:
                        telegram_status = "sent"
                        logging.info("event=telegram_send_success run_id=%s trace_id=%s symbol=%s tf=%s message_id=%s latency_ms=%d", self.run_id, trace_id, symbol, self.settings.test_emit_tf, tg_message_id if tg_message_id is not None else "na", tg_latency)
                    else:
                        telegram_status = "failed"
                        logging.warning("event=telegram_send_fail run_id=%s trace_id=%s symbol=%s tf=%s attempt=1 http_status=%s", self.run_id, trace_id, symbol, self.settings.test_emit_tf, tg_http if tg_http is not None else "na")

                    logging.info("event=postback_send_start run_id=%s trace_id=%s symbol=%s tf=%s", self.run_id, trace_id, symbol, self.settings.test_emit_tf)
                    pb_ok, pb_http, pb_latency = self.postback_client.send(payload)
                    if pb_ok:
                        postback_status = "sent"
                        logging.info("event=postback_send_success run_id=%s trace_id=%s symbol=%s tf=%s latency_ms=%d", self.run_id, trace_id, symbol, self.settings.test_emit_tf, pb_latency)
                    else:
                        postback_status = "failed"
                        logging.warning("event=postback_send_fail run_id=%s trace_id=%s symbol=%s tf=%s http_status=%s", self.run_id, trace_id, symbol, self.settings.test_emit_tf, pb_http if pb_http is not None else "na")

                    result = "success" if telegram_status == "sent" and postback_status == "sent" else ("partial" if telegram_status == "sent" or postback_status == "sent" else "fail")
                    logging.info("event=emit_pipeline_result run_id=%s trace_id=%s symbol=%s tf=%s result=%s card=true telegram=%s postback=%s total_ms=%d", self.run_id, trace_id, symbol, self.settings.test_emit_tf, result, telegram_status, postback_status, int((time.perf_counter() - started) * 1000))
                self.telegram_notifier.poll_updates_once()
            except Exception as exc:  # noqa: BLE001
                logging.exception("Main loop error (service continues): %s", exc)
            time.sleep(self.settings.poll_seconds)
