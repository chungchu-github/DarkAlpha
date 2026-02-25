from __future__ import annotations

import logging
import time

from .binance_client import BinanceFuturesClient
from .calculations import (
    aggregate_klines_to_window,
    atr_series,
    calculate_position_usdt,
    calculate_return,
)
from .config import Settings
from .models import ProposalCard
from .telegram_client import TelegramNotifier


def build_proposal_card(
    *,
    symbol: str,
    entry: float,
    return_5m: float,
    atr_15m: float,
    leverage_suggest: int,
    max_risk_usdt: float,
    ttl_minutes: int,
    rationale: str,
) -> ProposalCard:
    side = "LONG" if return_5m >= 0 else "SHORT"
    stop = entry - (1.2 * atr_15m) if side == "LONG" else entry + (1.2 * atr_15m)
    position_usdt = calculate_position_usdt(entry=entry, stop=stop, max_risk_usdt=max_risk_usdt)

    return ProposalCard.create(
        symbol=symbol,
        side=side,
        entry=entry,
        stop=stop,
        leverage_suggest=leverage_suggest,
        position_usdt=position_usdt,
        max_risk_usdt=max_risk_usdt,
        ttl_minutes=ttl_minutes,
        rationale=rationale,
    )


class SignalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.binance_client = BinanceFuturesClient()
        self.telegram_notifier = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )

    def evaluate_symbol(self, symbol: str) -> ProposalCard | None:
        price = self.binance_client.get_latest_price(symbol)
        klines_1m = self.binance_client.get_1m_klines(symbol, limit=self.settings.kline_limit)
        closes = [candle.close for candle in klines_1m]

        return_5m = calculate_return(closes, lookback_minutes=5)
        candles_15m = aggregate_klines_to_window(klines_1m, window=15)
        atr_values = atr_series(candles_15m, period=14)

        if not atr_values:
            logging.warning("Not enough data to compute ATR for %s", symbol)
            return None

        atr_latest = atr_values[-1]
        baseline_window = min(96, len(atr_values) - 1)
        if baseline_window <= 0:
            atr_baseline = atr_latest
        else:
            atr_baseline = sum(atr_values[-(baseline_window + 1) : -1]) / baseline_window

        return_trigger = abs(return_5m) > self.settings.return_threshold
        atr_trigger = atr_latest > (atr_baseline * self.settings.atr_spike_multiplier)

        logging.info(
            "%s metrics | price=%.3f return_5m=%.4f atr_15m=%.4f atr_baseline=%.4f",
            symbol,
            price,
            return_5m,
            atr_latest,
            atr_baseline,
        )

        if not (return_trigger or atr_trigger):
            return None

        rationale = (
            f"triggered: return_5m={return_5m:.4%} (th={self.settings.return_threshold:.2%}), "
            f"atr_15m={atr_latest:.4f} vs baseline={atr_baseline:.4f}"
        )
        card = build_proposal_card(
            symbol=symbol,
            entry=price,
            return_5m=return_5m,
            atr_15m=atr_latest,
            leverage_suggest=self.settings.leverage_suggest,
            max_risk_usdt=self.settings.max_risk_usdt,
            ttl_minutes=self.settings.ttl_minutes,
            rationale=rationale,
        )
        return card

    def run_forever(self) -> None:
        logging.info("Starting signal service for symbols: %s", self.settings.symbols)
        while True:
            for symbol in self.settings.symbols:
                try:
                    card = self.evaluate_symbol(symbol)
                    if card is not None:
                        self.telegram_notifier.send_json_card(card.to_dict())
                except Exception as exc:  # noqa: BLE001
                    logging.exception("Failed to evaluate symbol %s: %s", symbol, exc)
            time.sleep(self.settings.poll_seconds)
