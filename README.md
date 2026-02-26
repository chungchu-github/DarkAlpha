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

- `src/dark_alpha_phase_one/data/binance_ws.py`：Binance fapi WS client（bookTicker + kline_1m）
- `src/dark_alpha_phase_one/data/binance_rest.py`：REST client
- `src/dark_alpha_phase_one/data/datastore.py`：統一緩衝與 snapshot 介面
- `src/dark_alpha_phase_one/data/source_manager.py`：WS/REST 切換、stale 檢查、回切、state sync、健康摘要
- `src/dark_alpha_phase_one/data/datastore.py`：含 funding / open interest / mark price 統一快照
- `src/dark_alpha_phase_one/engine/signal_context.py`：統一訊號上下文
- `src/dark_alpha_phase_one/strategies/base.py`：策略介面
- `src/dark_alpha_phase_one/strategies/vol_breakout.py`：策略實作
- `src/dark_alpha_phase_one/service.py`：main loop 協調層（data -> strategy -> risk -> notify）

## 安裝

```bash
poetry env use 3.11
poetry install
cp .env.example .env
```

> 套件依賴包含 `websocket-client`（WS 連線）與 `requests`（REST fallback）。

## 設定 `.env`

### 策略/風控

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
- `PREMIUMINDEX_POLL_SECONDS=10`
- `FUNDING_POLL_SECONDS=60`
- `OI_POLL_SECONDS=10`
- `FUNDING_STALE_SECONDS=180`
- `OI_STALE_SECONDS=30`
- `FUNDING_EXTREME=0.0005`
- `OI_ZSCORE=2.0`
- `OI_DELTA_PCT=0.10`
- `SWEEP_PCT=0.001`
- `WICK_BODY_RATIO=2.0`
- `STOP_BUFFER_ATR=0.3`
- `MIN_ATR_PCT=0.001`

### 持久化

- `RISK_STATE_PATH=data/risk_state.json`
- `PNL_CSV_PATH=data/pnl.csv`

## 策略（插拔）

- `funding_oi_skew`：偵測 funding 極端 + OI zscore 擁擠，採反向反殺
- `liquidation_follow`：偵測 OI 15m 增幅 + 價格/ funding 同向，採順勢跟隨
- `vol_breakout_card`：原有波動突破策略（fallback）

## 假突破反殺策略（fake_breakout_reversal）

- 向上假突破：high 掃過前 20m 高點後收回（close < 前高）且上影/實體達閾值，生成 SHORT
- 向下假突破：low 跌破前 20m 低點後收回（close > 前低）且下影/實體達閾值，生成 LONG
- 風控 gating：ATR 太低或 kline close 資料過舊（>90s）不出卡
- 參數：`SWEEP_PCT`、`WICK_BODY_RATIO`、`STOP_BUFFER_ATR`、`MIN_ATR_PCT`

## 衍生品資料（Funding / OI）

- `premiumIndex`：markPrice / lastFundingRate / nextFundingTime（預設每 10 秒）
- `fundingRate history`：最近 N 次（預設 3 次，每 60 秒）
- `openInterest`：當前 OI（預設每 10 秒）
- 策略前置檢查：若 funding / OI 資料超過 stale 閾值，直接跳過卡片產生

## 模式切換規則

- 進入 fallback（`ws -> rest`）：
  - WS exception / 斷線
  - price stale (`> STALE_SECONDS`)
  - kline stale (`> KLINE_STALE_SECONDS`)
- 回切 primary（`rest -> ws`）：
  - WS 連續收到 `WS_RECOVER_GOOD_TICKS` 次新鮮 price ticks
  - 回切前先做 state sync：REST 補抓 `STATE_SYNC_KLINES` 合併進本地 kline buffer

> 每次模式切換都會 log `from -> to`、`reason`、`symbol`。每 60 秒輸出健康摘要（mode / age / buffer size）。
> 啟動時 state sync 為 best-effort，若 REST 暫時不可用會記錄警告但不讓服務啟動失敗。
> `kline_stale` 依據「1m kline close 更新」時間判定（僅 kline closed 事件更新 close timestamp）。

## 執行

```bash
poetry run python -m dark_alpha_phase_one.main
```

## 新增策略（3 步驟）

1. 在 `src/dark_alpha_phase_one/strategies/` 新增策略，繼承 `Strategy` 並實作 `generate(signal_context)`。
2. 在 `SignalService` 的 `self.strategies` 註冊策略（由前到後，第一個回傳 ProposalCard 的策略勝出）。
3. 在 `tests/` 新增該策略單元測試（至少覆蓋觸發/不觸發）。


## 策略優先序與去重規則

- 預設優先序（高到低）：
  - `fake_breakout_reversal` = 100
  - `funding_oi_skew` = 80
  - `liquidation_follow` = 60
  - `vol_breakout_card` = 40
- Arbitration 流程：
  1. 收集同一 symbol 所有候選卡
  2. dedupe window（同 symbol 最近 `DEDUPE_WINDOW_SECONDS` 有發過卡則本輪不推）
  3. 同 side 且 entry/stop 近似去重（`ENTRY_SIMILAR_PCT` / `STOP_SIMILAR_PCT`）
  4. 依 `priority` > `confidence` > `ttl_minutes(短者優先)` 選唯一贏家
- 置信度 `confidence(0~100)` 由各策略內部 heuristics 計算。
- 可調參數：
  - `DEDUPE_WINDOW_SECONDS`
  - `ENTRY_SIMILAR_PCT`
  - `STOP_SIMILAR_PCT`
  - `PRIORITY_FAKE_BREAKOUT`
  - `PRIORITY_FUNDING_OI_SKEW`
  - `PRIORITY_LIQUIDATION_FOLLOW`
  - `PRIORITY_VOL_BREAKOUT`

## 測試

```bash
pytest -q
```

## 如何模擬 WS 失效

- 最簡單：測試時注入 mock WS client，讓 `read_events()` 丟 exception。
- 服務層行為：`SourceManager` 會記錄切換 log，轉為 REST 輪詢並保持 main loop 持續。

