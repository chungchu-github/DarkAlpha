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
    kline_stale_ms: int
    ws_backoff_min: int
    ws_backoff_max: int
    rest_price_poll_seconds: float
    rest_kline_poll_seconds: float
    ws_recover_good_ticks: int
    state_sync_klines: int
    funding_poll_seconds: float
    premiumindex_poll_seconds: float
    oi_poll_seconds: float
    funding_stale_seconds: int
    oi_stale_seconds: int
    funding_stale_ms: int
    oi_stale_ms: int
    max_clock_error_ms: int
    server_time_refresh_sec: int
    server_time_degraded_retry_sec: int
    clock_refresh_cooldown_ms: int
    clock_degraded_ttl_ms: int
    funding_extreme: float
    oi_zscore: float
    oi_delta_pct: float
    sweep_pct: float
    wick_body_ratio: float
    stop_buffer_atr: float
    min_atr_pct: float
    dedupe_window_seconds: int
    entry_similar_pct: float
    stop_similar_pct: float
    priority_fake_breakout: int
    priority_funding_oi_skew: int
    priority_liquidation_follow: int
    priority_vol_breakout: int


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
        kline_limit=int(os.getenv("KLINE_LIMIT", "500")),
        max_daily_loss_usdt=float(os.getenv("MAX_DAILY_LOSS_USDT", "30")),
        max_cards_per_day=int(os.getenv("MAX_CARDS_PER_DAY", "5")),
        cooldown_after_trigger_minutes=int(os.getenv("COOLDOWN_AFTER_TRIGGER_MINUTES", "30")),
        kill_switch=os.getenv("KILL_SWITCH", "0") == "1",
        risk_state_path=os.getenv("RISK_STATE_PATH", "data/risk_state.json"),
        pnl_csv_path=os.getenv("PNL_CSV_PATH", "data/pnl.csv"),
        data_source_preferred=os.getenv("DATA_SOURCE_PREFERRED", "ws"),
        stale_seconds=int(os.getenv("STALE_SECONDS", "5")),
        kline_stale_seconds=int(os.getenv("KLINE_STALE_SECONDS", "30")),
        kline_stale_ms=int(os.getenv("KLINE_STALE_MS", str(int(os.getenv("KLINE_STALE_SECONDS", "30")) * 1000))),
        ws_backoff_min=int(os.getenv("WS_BACKOFF_MIN", "1")),
        ws_backoff_max=int(os.getenv("WS_BACKOFF_MAX", "60")),
        rest_price_poll_seconds=float(os.getenv("REST_PRICE_POLL_SECONDS", "1")),
        rest_kline_poll_seconds=float(os.getenv("REST_KLINE_POLL_SECONDS", "10")),
        ws_recover_good_ticks=int(os.getenv("WS_RECOVER_GOOD_TICKS", "3")),
        state_sync_klines=max(120, int(os.getenv("STATE_SYNC_KLINES", os.getenv("KLINE_LIMIT", "500")))),
        funding_poll_seconds=float(os.getenv("FUNDING_POLL_SECONDS", "60")),
        premiumindex_poll_seconds=float(os.getenv("PREMIUMINDEX_POLL_SECONDS", "10")),
        oi_poll_seconds=float(os.getenv("OI_POLL_SECONDS", "10")),
        funding_stale_seconds=int(os.getenv("FUNDING_STALE_SECONDS", "180")),
        oi_stale_seconds=int(os.getenv("OI_STALE_SECONDS", "180")),
        funding_stale_ms=int(os.getenv("FUNDING_STALE_MS", str(int(os.getenv("FUNDING_STALE_SECONDS", "180")) * 1000))),
        oi_stale_ms=int(os.getenv("OI_STALE_MS", str(int(os.getenv("OI_STALE_SECONDS", "180")) * 1000))),
        max_clock_error_ms=int(os.getenv("MAX_CLOCK_ERROR_MS", "5000")),
        server_time_refresh_sec=int(os.getenv("SERVER_TIME_REFRESH_SEC", "60")),
        server_time_degraded_retry_sec=int(os.getenv("SERVER_TIME_DEGRADED_RETRY_SEC", "10")),
        clock_refresh_cooldown_ms=int(os.getenv("CLOCK_REFRESH_COOLDOWN_MS", "30000")),
        clock_degraded_ttl_ms=int(os.getenv("CLOCK_DEGRADED_TTL_MS", "60000")),
        funding_extreme=float(os.getenv("FUNDING_EXTREME", "0.0005")),
        oi_zscore=float(os.getenv("OI_ZSCORE", "2.0")),
        oi_delta_pct=float(os.getenv("OI_DELTA_PCT", "0.10")),
        sweep_pct=float(os.getenv("SWEEP_PCT", "0.001")),
        wick_body_ratio=float(os.getenv("WICK_BODY_RATIO", "2.0")),
        stop_buffer_atr=float(os.getenv("STOP_BUFFER_ATR", "0.3")),
        min_atr_pct=float(os.getenv("MIN_ATR_PCT", "0.001")),
        dedupe_window_seconds=int(os.getenv("DEDUPE_WINDOW_SECONDS", "300")),
        entry_similar_pct=float(os.getenv("ENTRY_SIMILAR_PCT", "0.001")),
        stop_similar_pct=float(os.getenv("STOP_SIMILAR_PCT", "0.001")),
        priority_fake_breakout=int(os.getenv("PRIORITY_FAKE_BREAKOUT", "100")),
        priority_funding_oi_skew=int(os.getenv("PRIORITY_FUNDING_OI_SKEW", "80")),
        priority_liquidation_follow=int(os.getenv("PRIORITY_LIQUIDATION_FOLLOW", "60")),
        priority_vol_breakout=int(os.getenv("PRIORITY_VOL_BREAKOUT", "40")),
    )
