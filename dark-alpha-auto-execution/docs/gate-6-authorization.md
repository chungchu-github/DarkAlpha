# Gate 6 Mainnet Micro-Live Authorization

> **Canary 3** — third micro-live canary, **Path C 3-gate version
> Gate A** (system actually trades correctly: real fill chain +
> scale-up combined). Same-day compression after canary 2 0 PnL,
> with Bug 4 fix (commit `c8985b8`) deployed. Entry distance
> tightened to 0.2% (CLI `--entry-offset-pct 0.002`) to maximise
> probability of real fill, which canary 1 + 2 never reached.

- Generated at UTC: `2026-05-05T12:52:00Z`
- Operator: `darkagent001`

## Operator Limits

- Authorized symbol: `ETHUSDT-PERP`
- Direction allowed: `long`
- Strategy allowed: `manual_test_signal` (Gate 6 canary submission only — automated signals service stays off for canary 3)
- Max notional per order USDT: `100`
- Max leverage: `1`
- Max concurrent positions: `1`
- Max daily loss USDT: `5`
- Exercise window start UTC: `2026-05-05T13:00:00Z`
- Exercise window end UTC: `2026-05-05T13:30:00Z`
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
    exercise_window_start: "2026-05-05T13:00:00Z"
    exercise_window_end:   "2026-05-05T13:30:00Z"
    auto_cancel_flatten_after: true
```

## Rationale for these limits

Path C 3-gate version, Gate A (合併原 #1 fill chain + #2 scale-up).
4× notional vs canary 2 ($25 → $100), entry distance 0.5% → 0.2%
to force fill probability. Stop / TP distance both 1% (default).
Daily-loss cap stays $5 (~$25 = 5 stop-outs of headroom at $100
notional × 1% stop, since stop loss = -$1 per fire).

Operator explicitly accepts 5 stacked risk multipliers
(time-compression vs canary 2, $100 margin = ~100% account use,
violation of own 24h cool-down rule, Bug 4 fix not yet
production-validated, NY equity open at 13:30 UTC = closeout).
Recommended path was $50 reduced cap; operator chose $100 for
maximum information density per Path C 3-gate Gate A definition.

## Signature

- Operator: `darkagent001`
- Date: `2026-05-05`
- Notes: `Canary 3 (13:00-13:30 UTC = 21:00-21:30 local UTC+8) — Path C 3-gate Gate A. Same-day compression after canary 2 0 PnL closeout at 12:30:57 UTC. Bug 4 fix (c8985b8) live for first time; 416/416 unit tests pass. Entry 0.2% to force fill. NY open at 13:30 UTC = closeout time, accepted risk.`
