from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from .calculations import Candle, aggregate_klines_to_window, atr_series, calculate_return
from .config import Settings
from .data.binance_rest import BinanceRestDataClient
from .data.binance_ws import BinanceWsClient
from .data.datastore import DataStore
from .data.source_manager import SourceManager
from .engine.signal_context import SignalContext
from .models import ProposalCard
from .risk_engine import RiskEngine
from .strategies.base import Strategy
from .strategies.vol_breakout import VolBreakoutStrategy
from .telegram_client import TelegramNotifier


def build_signal_context(
    *,
    symbol: str,
    price: float,
    klines_1m: list[Candle],
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

    return SignalContext(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        price=price,
        klines_1m=klines_1m,
        return_5m=return_5m,
        atr_15m=atr_15m,
        atr_15m_baseline=atr_baseline,
    )


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
        self.strategies: list[Strategy] = [
            VolBreakoutStrategy(
                return_threshold=settings.return_threshold,
                atr_spike_multiplier=settings.atr_spike_multiplier,
                leverage_suggest=settings.leverage_suggest,
                max_risk_usdt=settings.max_risk_usdt,
                ttl_minutes=settings.ttl_minutes,
            )
        ]

    def evaluate_symbol(self, symbol: str) -> ProposalCard | None:
        snapshot = self.datastore.snapshot(symbol)
        if snapshot.price is None or not snapshot.klines_1m:
            logging.debug("Data not ready for %s in mode=%s", symbol, snapshot.data_source_mode)
            return None

        signal_context = build_signal_context(symbol=symbol, price=snapshot.price, klines_1m=snapshot.klines_1m)
        if signal_context is None:
            logging.warning("Not enough data to compute ATR for %s", symbol)
            return None

        logging.info(
            "%s metrics | mode=%s price=%.3f return_5m=%.4f atr_15m=%.4f atr_baseline=%.4f",
            symbol,
            snapshot.data_source_mode,
            signal_context.price,
            signal_context.return_5m,
            signal_context.atr_15m,
            signal_context.atr_15m_baseline,
        )

        card = self._run_strategies(signal_context)
        if card is None:
            return None

        risk_decision = self.risk_engine.evaluate(symbol)
        if not risk_decision.allowed:
            logging.info("Risk blocked %s: %s", symbol, risk_decision.reason)
            return None

        self.risk_engine.record_trigger(symbol)
        return card

    def _run_strategies(self, signal_context: SignalContext) -> ProposalCard | None:
        for strategy in self.strategies:
            card = strategy.generate(signal_context)
            if card is not None:
                logging.info("%s selected by strategy %s", signal_context.symbol, strategy.name)
                return card
        return None

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
