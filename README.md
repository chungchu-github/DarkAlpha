# DarkAlphaPhaseOne

Python 3.11 MVP 專案：連接 Binance USDT-M Futures（BTCUSDT / ETHUSDT），每秒抓取最新價格 + 1m Kline，計算 5m 報酬率與 15m ATR，符合條件就生成提案卡 JSON 並透過 Telegram 發送。

## 功能摘要

- 資料來源：Binance Futures REST API
  - `GET /fapi/v1/ticker/price`
  - `GET /fapi/v1/klines?interval=1m`
- 指標：
  - `5m return = (close_now - close_5m_ago) / close_5m_ago`
  - `15m ATR`：以 1m kline 聚合成 15m kline，ATR period=14
- 觸發條件（任一成立）：
  - `abs(5m return) > 1.2%`
  - `15m ATR > 最近 24h 的 15m ATR 均值 * 2.0`
    - 若資料不足，改用可得的最近 N 個 ATR 視窗近似
- 提案卡 schema：
  - `symbol, strategy, side, entry, stop, leverage_suggest, position_usdt, max_risk_usdt, ttl_minutes, rationale, created_at`

## 專案結構

- `src/dark_alpha_phase_one/calculations.py`：回報率、聚合、ATR、倉位計算
- `src/dark_alpha_phase_one/binance_client.py`：Binance REST 客戶端
- `src/dark_alpha_phase_one/service.py`：訊號判斷與提案卡產生
- `src/dark_alpha_phase_one/telegram_client.py`：Telegram 發送
- `src/dark_alpha_phase_one/main.py`：啟動入口
- `tests/`：pytest 單元測試

## 安裝（Poetry）

```bash
poetry env use 3.11
poetry install
cp .env.example .env
```

## 設定 `.env`

至少設定：

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

其他常用參數：

- `SYMBOLS=BTCUSDT,ETHUSDT`
- `POLL_SECONDS=1`
- `RETURN_THRESHOLD=0.012`
- `ATR_SPIKE_MULTIPLIER=2.0`
- `MAX_RISK_USDT=10`
- `LEVERAGE_SUGGEST=50`
- `TTL_MINUTES=15`
- `KLINE_LIMIT=300`

## 執行

```bash
poetry run python -m dark_alpha_phase_one.main
```

## 公式細節

- `side`：`5m return > 0 -> LONG`，否則 SHORT（MVP 中 `=0` 視為 LONG）
- `entry`：當前價格
- `stop`：
  - LONG: `entry - (1.2 * ATR_15m)`
  - SHORT: `entry + (1.2 * ATR_15m)`
- `position_usdt`：
  - `max_risk_usdt / (abs(entry-stop)/entry)`
- `strategy`：固定 `vol_breakout_card`

## 測試

```bash
poetry run pytest -q
```

## Docker Compose（可選）

```bash
docker compose up --build
```

