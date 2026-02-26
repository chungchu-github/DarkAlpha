from __future__ import annotations

from datetime import datetime, timezone
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
from .strategies.base import Strategy
from .strategies.fake_breakout_reversal import FakeBreakoutReversalStrategy
from .strategies.funding_oi_skew import FundingOiSkewStrategy
from .strategies.liquidation_follow import LiquidationFollowStrategy
from .strategies.vol_breakout import VolBreakoutStrategy
from .telegram_client import TelegramNotifier


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
    candles_15m = aggregate_klines_to_window(klines_1m, window=15)
    atr_values = atr_series(candles_15m, period=14)

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


def derivatives_are_fresh(snapshot: SymbolSnapshot, funding_stale_seconds: int, oi_stale_seconds: int) -> bool:
    now = datetime.now(tz=timezone.utc)
    if snapshot.funding_ts is None or snapshot.open_interest_ts is None:
        return False
    funding_age = (now - snapshot.funding_ts).total_seconds()
    oi_age = (now - snapshot.open_interest_ts).total_seconds()
    return funding_age <= funding_stale_seconds and oi_age <= oi_stale_seconds


class SignalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
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
        )
        self.telegram_notifier = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
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

    def evaluate_symbol(self, symbol: str) -> ProposalCard | None:
        snapshot = self.datastore.snapshot(symbol)
        if snapshot.price is None or not snapshot.klines_1m:
            logging.debug("Data not ready for %s in mode=%s", symbol, snapshot.data_source_mode)
            return None

        if not derivatives_are_fresh(snapshot, self.settings.funding_stale_seconds, self.settings.oi_stale_seconds):
            logging.info("Derivatives stale for %s, skip card generation", symbol)
            return None

        if snapshot.last_funding_rate is None or snapshot.open_interest is None or snapshot.mark_price is None:
            logging.info("Derivatives missing for %s, skip card generation", symbol)
            return None

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
            logging.warning("Not enough data to compute ATR for %s", symbol)
            return None

        candidates = self._collect_strategy_cards(signal_context)
        card = self.arbitrator.choose_best(candidates, signal_context)
        if card is None:
            return None

        risk_decision = self.risk_engine.evaluate(symbol)
        if not risk_decision.allowed:
            logging.info("Risk blocked %s: %s", symbol, risk_decision.reason)
            return None

        self.risk_engine.record_trigger(symbol)
        return card

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
                    card = self.evaluate_symbol(symbol)
                    if card is not None:
                        self.telegram_notifier.send_json_card(card.to_dict())
            except Exception as exc:  # noqa: BLE001
                logging.exception("Main loop error (service continues): %s", exc)
            time.sleep(self.settings.poll_seconds)
