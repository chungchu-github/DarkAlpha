# DarkAlphaPhaseOne

Python 3.11 MVP 專案：連接 Binance USDT-M Futures（BTCUSDT / ETHUSDT），以 **WebSocket 優先 + REST fallback** 持續供應資料，計算 5m 報酬率與 15m ATR，符合條件就生成提案卡 JSON 並透過 Telegram 發送。

## 功能摘要

- 資料來源（自動切換）：
  - Primary: WebSocket
  - Fallback: REST（不中斷服務）
- 指標：
  - `5m return = (close_now - close_5m_ago) / close_5m_ago`
  - `15m ATR`：1m kline 聚合為 15m kline，ATR period=14
- 觸發條件（任一成立）：
  - `abs(5m return) > 1.2%`
  - `15m ATR > 最近 24h 的 15m ATR 均值 * 2.0`（不足時用最近 N 視窗近似）
- 內建 Risk Engine：
  - `max_daily_loss_usdt=30`
  - `max_cards_per_day=5`
  - `cooldown_after_trigger_minutes=30`
  - `KILL_SWITCH=1` 完全停止推送

## 專案結構

- `src/dark_alpha_phase_one/data/binance_ws.py`：WS client 抽象
- `src/dark_alpha_phase_one/data/binance_rest.py`：REST client
- `src/dark_alpha_phase_one/data/datastore.py`：統一緩衝與 snapshot 介面
- `src/dark_alpha_phase_one/data/source_manager.py`：WS/REST 切換、stale 檢查、回切、state sync、健康摘要
- `src/dark_alpha_phase_one/engine/signal_context.py`：統一訊號上下文
- `src/dark_alpha_phase_one/strategies/base.py`：策略介面
- `src/dark_alpha_phase_one/strategies/vol_breakout.py`：策略實作
- `src/dark_alpha_phase_one/service.py`：main loop 協調層（data -> strategy -> risk -> notify）

## 安裝
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

### 策略/風控
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
- `MAX_DAILY_LOSS_USDT=30`
- `MAX_CARDS_PER_DAY=5`
- `COOLDOWN_AFTER_TRIGGER_MINUTES=30`
- `KILL_SWITCH=0|1`

### Data Source（WS 優先 + fallback）

- `DATA_SOURCE_PREFERRED=ws`
- `STALE_SECONDS=5`
- `KLINE_STALE_SECONDS=30`
- `WS_BACKOFF_MIN=1`
- `WS_BACKOFF_MAX=60`
- `REST_PRICE_POLL_SECONDS=1`
- `REST_KLINE_POLL_SECONDS=10`
- `WS_RECOVER_GOOD_TICKS=3`
- `STATE_SYNC_KLINES=120`

### 持久化

- `RISK_STATE_PATH=data/risk_state.json`
- `PNL_CSV_PATH=data/pnl.csv`

## 模式切換規則

- 進入 fallback（`ws -> rest`）：
  - WS exception / 斷線
  - price stale (`> STALE_SECONDS`)
  - kline stale (`> KLINE_STALE_SECONDS`)
- 回切 primary（`rest -> ws`）：
  - WS 連續收到 `WS_RECOVER_GOOD_TICKS` 次新鮮 price ticks
  - 回切前先做 state sync：REST 補抓 `STATE_SYNC_KLINES` 合併進本地 kline buffer

> 每次模式切換都會 log `from -> to`、`reason`、`symbol`。每 60 秒輸出健康摘要（mode / age / buffer size）。
- `KLINE_LIMIT=300`

## 執行

```bash
poetry run python -m dark_alpha_phase_one.main
```

## 新增策略（3 步驟）

1. 在 `src/dark_alpha_phase_one/strategies/` 新增策略，繼承 `Strategy` 並實作 `generate(signal_context)`。
2. 在 `SignalService` 的 `self.strategies` 註冊策略（由前到後，第一個回傳 ProposalCard 的策略勝出）。
3. 在 `tests/` 新增該策略單元測試（至少覆蓋觸發/不觸發）。
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
pytest -q
```

## 如何模擬 WS 失效

- 最簡單：測試時注入 mock WS client，讓 `read_price_ticks()` 丟 exception。
- 服務層行為：`SourceManager` 會記錄切換 log，轉為 REST 輪詢並保持 main loop 持續。
poetry run pytest -q
```

## Docker Compose（可選）

```bash
docker compose up --build
```

