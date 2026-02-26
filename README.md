# DarkAlphaPhaseOne

DarkAlphaPhaseOne 是一個 **Binance USDT-M Futures 訊號提案服務**：
系統會持續拉取市場資料、計算指標、執行多策略、做仲裁與風控，最後把唯一提案卡（JSON）推送到 Telegram。

> 目前輸出為「提案卡」，**不下單**。

---

## 1. 核心能力

- 支援交易對：預設 `BTCUSDT,ETHUSDT`
- 資料來源：**WebSocket 優先，REST 自動 fallback 與 recover**
- 指標計算：5m return、15m ATR、15m OI z-score、15m OI delta%
- 策略：
  - `fake_breakout_reversal`
  - `funding_oi_skew`
  - `liquidation_follow`
  - `vol_breakout_card`
- 決策流程：**多策略同時產生候選卡 → 仲裁選唯一卡 → 風控檢查 → 推送 Telegram**

---

## 2. 架構總覽

```text
src/dark_alpha_phase_one/
  data/
    binance_ws.py          # Binance Futures WS client（bookTicker + kline_1m）
    binance_rest.py        # REST data client（price, klines, premiumIndex, funding, OI）
    datastore.py           # thread-safe 緩衝與快照介面
    source_manager.py      # WS/REST 模式切換、stale 檢查、recover、state sync
  engine/
    signal_context.py      # 策略統一輸入
    arbitrator.py          # 候選卡去重 + 互斥 + 決勝
  strategies/
    base.py
    fake_breakout_reversal.py
    funding_oi_skew.py
    liquidation_follow.py
    vol_breakout.py
  calculations.py          # 指標與倉位計算
  risk_engine.py           # kill-switch / daily limits / cooldown / state persistence
  service.py               # 主流程協調
  telegram_client.py       # Telegram 推送
  main.py                  # 程式入口
```

---

## 3. 執行流程（每輪）

對每個 symbol：

1. `SourceManager.refresh()` 更新 DataStore（WS 或 REST）
2. 建立 `SignalContext`
3. 跑所有策略，收集候選 `ProposalCard`
4. `Arbitrator.choose_best(...)` 選出唯一卡
5. `RiskEngine.evaluate(...)` 風控檢查
6. 通過才 `record_trigger` 並送 Telegram

這表示目前不是「第一個策略先回傳就直接採用」，而是先收集後仲裁。

---

## 4. 安裝與啟動

### 4.1 環境需求

- Python 3.11
- Poetry

### 4.2 安裝

```bash
poetry env use 3.11
poetry install
cp .env.example .env
```

### 4.3 啟動

```bash
poetry run python -m dark_alpha_phase_one.main
```

### 4.4 測試

```bash
pytest -q
```

---

## 5. 設定參數（.env）

以下列出常用參數；完整清單請看 `.env.example`。

### 5.1 基礎與通知

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SYMBOLS=BTCUSDT,ETHUSDT`
- `POLL_SECONDS=1`

### 5.2 風控

- `MAX_DAILY_LOSS_USDT=30`
- `MAX_CARDS_PER_DAY=5`
- `COOLDOWN_AFTER_TRIGGER_MINUTES=30`
- `KILL_SWITCH=0`
- `RISK_STATE_PATH=data/risk_state.json`
- `PNL_CSV_PATH=data/pnl.csv`

### 5.3 資料來源與回切

- `KLINE_LIMIT=500`（建議至少 210，避免 ATR warmup 不足）
- `DATA_SOURCE_PREFERRED=ws`
- `STALE_SECONDS=5`
- `KLINE_STALE_SECONDS=30`
- `KLINE_STALE_MS=30000`（WS kline freshness 使用「最後收到 kline 訊息時間」）
- `WS_BACKOFF_MIN=1`
- `WS_BACKOFF_MAX=60`
- `REST_PRICE_POLL_SECONDS=1`
- `REST_KLINE_POLL_SECONDS=10`
- `WS_RECOVER_GOOD_TICKS=3`
- `STATE_SYNC_KLINES=500`

### 5.4 衍生品資料輪詢與 stale gating

- `PREMIUMINDEX_POLL_SECONDS=10`
- `FUNDING_POLL_SECONDS=60`
- `OI_POLL_SECONDS=10`
- `FUNDING_STALE_SECONDS=180`
- `OI_STALE_SECONDS=30`
- `FUNDING_STALE_MS=180000`
- `OI_STALE_MS=30000`
- `MAX_CLOCK_ERROR_MS=1000`（clock sanity check 容忍誤差）
- `SERVER_TIME_REFRESH_SEC=60`（正常同步週期）
- `SERVER_TIME_DEGRADED_RETRY_SEC=10`（degraded 狀態重試週期）

> funding/OI 若過舊，服務會跳過提案卡生成。

### 5.5 策略參數

- Vol breakout
  - `RETURN_THRESHOLD=0.012`
  - `ATR_SPIKE_MULTIPLIER=2.0`
  - `LEVERAGE_SUGGEST=50`
  - `TTL_MINUTES=15`
- Funding / OI
  - `FUNDING_EXTREME=0.0005`
  - `OI_ZSCORE=2.0`
  - `OI_DELTA_PCT=0.10`
- Fake breakout reversal
  - `SWEEP_PCT=0.001`
  - `WICK_BODY_RATIO=2.0`
  - `STOP_BUFFER_ATR=0.3`
  - `MIN_ATR_PCT=0.001`

### 5.6 仲裁與去重

- `DEDUPE_WINDOW_SECONDS=300`
- `ENTRY_SIMILAR_PCT=0.001`
- `STOP_SIMILAR_PCT=0.001`
- `PRIORITY_FAKE_BREAKOUT=100`
- `PRIORITY_FUNDING_OI_SKEW=80`
- `PRIORITY_LIQUIDATION_FOLLOW=60`
- `PRIORITY_VOL_BREAKOUT=40`

---

## 6. 策略說明

### 6.1 fake_breakout_reversal（高優先）

偵測「掃流動性插針後快速回收」：
- sweep high 並 reclaim → `SHORT`
- sweep low 並 reclaim → `LONG`
- 需要 wick/body 條件成立
- 會檢查 ATR 最低門檻與 kline 新鮮度

### 6.2 funding_oi_skew

- 條件：`abs(funding)` 極端 + `OI z-score` 高 + 價格與 funding 方向一致（擁擠）
- 邏輯：做反向（crowded long → short；crowded short → long）

### 6.3 liquidation_follow

- 條件：`OI delta 15m` 增幅達標 + `abs(5m return)` 達標 + funding 與價格同向
- 邏輯：順勢跟隨

### 6.4 vol_breakout_card（fallback）

- 條件：`abs(5m return)` 超過門檻，或 `ATR` 相對基準放大
- 作為穩定基礎策略

---

## 7. 策略優先序與去重規則

同一輪同一 symbol 若有多張候選卡：

1. 先做 dedupe / 互斥
   - dedupe window 內近期已推送過卡：直接不推
   - 同 side 且 entry/stop 高度相似：只保留較佳候選
2. 決勝順序
   - `priority` 高者勝
   - 若相同：`confidence` 高者勝
   - 若仍相同：`ttl_minutes` 較短者勝

預設 priority（高 → 低）：
`fake_breakout_reversal` > `funding_oi_skew` > `liquidation_follow` > `vol_breakout_card`

---

## 8. 風控規則

- `KILL_SWITCH=1`：完全停止推送
- 當日累計虧損超過 `MAX_DAILY_LOSS_USDT`：停止推送
- 當日提案數超過 `MAX_CARDS_PER_DAY`：停止推送
- 同 symbol 在 cooldown 內：停止推送

風控 state 會持久化到 `RISK_STATE_PATH`，重啟後可延續。

---

## 9. 可用性與穩定性設計

- health log 會同時輸出 `*_raw_age_ms` 與 `*_age_seconds`，若 timestamp 在未來會記錄 `timestamp_in_future` 告警
- health log 額外輸出 `clock_state` 與 `last_server_sync_age_ms`，便於觀測時鐘同步狀態
- WS 異常、price stale、kline stale 都會觸發 `ws -> rest`
- REST 模式下持續嘗試 WS 重連（exponential backoff）
- WS 恢復達標後 `rest -> ws`，切回前先做 state sync 補 K 線
- 主循環採「記錄錯誤 + 繼續」，避免單次例外讓服務中止

---

## 10. 如何新增策略（3 步驟）

1. 在 `src/dark_alpha_phase_one/strategies/` 新增策略檔案，實作 `Strategy.generate(ctx) -> ProposalCard | None`
2. 在 `SignalService` 的 `self.strategies` 註冊策略，並給定對應 priority
3. 補上該策略的單元測試（至少觸發/不觸發兩條）

---

## 11. Docker（可選）

```bash
docker compose up --build
```

---

## 12. 安全與部署建議

- 僅將 token/key 放在 `.env`，勿提交到 git
- 建議將 `poetry.lock` 納入版控，確保部署依賴可重現
- 正式上線前先做 staging/paper soak test（建議 24h+）
