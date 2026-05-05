# Gate 6 Mainnet Micro-Live Authorization

> **Canary 4** — fourth micro-live canary, **Path C 3-gate Gate A
> retry** (real fill chain still unproven after 3 no-fill canaries).
> Tactic change: **60-min window** (2× canary 1-3) + entry **0.1%**
> (half of canary 3's 0.2%) to maximise probability of fill →
> bracket activation → SL/TP fire → close → real PnL accounting,
> which is Gate A's blocking deliverable. Window 14:00-15:00 UTC =
> NY equity peak morning hours (operator-accepted volatility).

- Generated at UTC: `2026-05-05T13:32:00Z`
- Operator: `darkagent001`

## Operator Limits

- Authorized symbol: `ETHUSDT-PERP`
- Direction allowed: `long`
- Strategy allowed: `manual_test_signal` (Gate 6 canary submission only)
- Max notional per order USDT: `100`
- Max leverage: `1`
- Max concurrent positions: `1`
- Max daily loss USDT: `5`
- Exercise window start UTC: `2026-05-05T14:00:00Z`
- Exercise window end UTC: `2026-05-05T15:00:00Z`
- Auto cancel-all after window: `yes`
- Auto flatten after window: `yes`

## Safety Acknowledgement

- [x] Mainnet key is dedicated to this bot.
- [x] Mainnet key has no withdrawal permission.
- [x] Mainnet key is IP restricted when possible.
- [x] `poetry run dark-alpha gate-check all` passed immediately before the run.
- [x] Binance account has no unknown open orders.
- [x] Binance account has no unknown position.
- [x] Every live ticket must include stop loss and take profit.
- [x] Operator accepts that this is a micro-live canary, not production live trading.
- [x] Dashboard 1–2h soak test passed (see `docs/dashboard-soak-test.md`).

## Matching `config/main.yaml` Block

> Operator applies this block to `config/main.yaml` immediately
> before the window opens, and reverts to `mode: shadow` (or
> `live/testnet`) immediately after closeout. **Do not commit the
> mainnet-armed main.yaml.**

```yaml
mode: live
live:
  environment: mainnet
  allow_mainnet: true
  require_gate_authorization: true
  gate_authorization_file: docs/gate-6-authorization.md
  micro_live:
    enabled: true
    allowed_symbols:
      - ETHUSDT-PERP
    max_notional_usd: 100
    max_leverage: 1
    max_daily_loss_usd: 5
    max_concurrent_positions: 1
    require_stop_loss: true
    require_take_profit: true
    exercise_window_start: "2026-05-05T14:00:00Z"
    exercise_window_end:   "2026-05-05T15:00:00Z"
    auto_cancel_flatten_after: true
```

## Rationale for these limits

Three consecutive no-fill canaries (1+2+3) at entry distances
0.5% / 0.5% / 0.2% in 30-min calm UTC windows. Tactic change for
canary 4:

- **60 min window** (2×) — gives ETH twice the time to walk to
  entry. NY equity now open (since 13:30 UTC), so volatility is
  elevated — operator accepts this as the cost of finally reaching
  fill chain.
- **Entry 0.1%** (half of canary 3) — closer entry on absolute basis.
  Small enough to fill on micro-direction moves, large enough to
  not be flicker-only. Stop / TP both stay at default 1%.

Same $100 notional as canary 3 (no further scale change in same
canary — Path C discipline). Direction stays `long` for
reproducibility against canary 3.

Expected outcomes if fill occurs:
- TP hit: +~$1 (1% × $100)
- SL hit: -~$1
- Either way, this is the **first real PnL sample** in path C.

Same-day fourth canary back-to-back; operator explicitly accepts
the further compression.

## Signature

- Operator: `darkagent001`
- Date: `2026-05-05`
- Notes: `Canary 4 (14:00-15:00 UTC = 22:00-23:00 local UTC+8) — Path C 3-gate Gate A retry. 60-min window + entry 0.1% to actually reach fill chain. NY equity peak morning (open 13:30 UTC) accepted as the cost of fill probability. Bug 4 fix proven in canary 3.`
