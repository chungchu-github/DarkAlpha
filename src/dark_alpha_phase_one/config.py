from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    binance_api_key: str | None
    binance_api_secret: str | None
    symbols: list[str]
    poll_seconds: float
    return_threshold: float
    atr_spike_multiplier: float
    max_risk_usdt: float
    leverage_suggest: int
    ttl_minutes: int
    kline_limit: int


def load_settings() -> Settings:
    load_dotenv()
    symbols = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT")

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        binance_api_key=os.getenv("BINANCE_API_KEY"),
        binance_api_secret=os.getenv("BINANCE_API_SECRET"),
        symbols=[symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()],
        poll_seconds=float(os.getenv("POLL_SECONDS", "1")),
        return_threshold=float(os.getenv("RETURN_THRESHOLD", "0.012")),
        atr_spike_multiplier=float(os.getenv("ATR_SPIKE_MULTIPLIER", "2.0")),
        max_risk_usdt=float(os.getenv("MAX_RISK_USDT", "10")),
        leverage_suggest=int(os.getenv("LEVERAGE_SUGGEST", "50")),
        ttl_minutes=int(os.getenv("TTL_MINUTES", "15")),
        kline_limit=int(os.getenv("KLINE_LIMIT", "300")),
    )
