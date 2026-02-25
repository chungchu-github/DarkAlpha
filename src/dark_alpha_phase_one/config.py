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
    max_daily_loss_usdt: float
    max_cards_per_day: int
    cooldown_after_trigger_minutes: int
    kill_switch: bool
    risk_state_path: str
    pnl_csv_path: str | None
    data_source_preferred: str
    stale_seconds: int
    kline_stale_seconds: int
    ws_backoff_min: int
    ws_backoff_max: int
    rest_price_poll_seconds: float
    rest_kline_poll_seconds: float
    ws_recover_good_ticks: int
    state_sync_klines: int


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
        max_daily_loss_usdt=float(os.getenv("MAX_DAILY_LOSS_USDT", "30")),
        max_cards_per_day=int(os.getenv("MAX_CARDS_PER_DAY", "5")),
        cooldown_after_trigger_minutes=int(os.getenv("COOLDOWN_AFTER_TRIGGER_MINUTES", "30")),
        kill_switch=os.getenv("KILL_SWITCH", "0") == "1",
        risk_state_path=os.getenv("RISK_STATE_PATH", "data/risk_state.json"),
        pnl_csv_path=os.getenv("PNL_CSV_PATH", "data/pnl.csv"),
        data_source_preferred=os.getenv("DATA_SOURCE_PREFERRED", "ws"),
        stale_seconds=int(os.getenv("STALE_SECONDS", "5")),
        kline_stale_seconds=int(os.getenv("KLINE_STALE_SECONDS", "30")),
        ws_backoff_min=int(os.getenv("WS_BACKOFF_MIN", "1")),
        ws_backoff_max=int(os.getenv("WS_BACKOFF_MAX", "60")),
        rest_price_poll_seconds=float(os.getenv("REST_PRICE_POLL_SECONDS", "1")),
        rest_kline_poll_seconds=float(os.getenv("REST_KLINE_POLL_SECONDS", "10")),
        ws_recover_good_ticks=int(os.getenv("WS_RECOVER_GOOD_TICKS", "3")),
        state_sync_klines=int(os.getenv("STATE_SYNC_KLINES", "120")),
    )
