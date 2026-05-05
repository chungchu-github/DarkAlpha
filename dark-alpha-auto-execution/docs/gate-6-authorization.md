# Gate 6 Mainnet Micro-Live Authorization

> **Canary 2** — second micro-live canary, post-canary-1 fixes
> (commits `a16eb66` exchangeInfo bug, `07a4800` cleanup grace).
> Same caps as canary 1 retry; window shifted to 12:00-12:30 UTC
> on 2026-05-05 (operator evening 20:00-20:30 local UTC+8, EU
> lunch lull, before NY pre-market).

- Generated at UTC: `2026-05-05T11:47:00Z`
- Operator: `darkagent001`

## Operator Limits

- Authorized symbol: `ETHUSDT-PERP`
- Direction allowed: `long`
- Strategy allowed: `manual_test_signal` (Gate 6 canary submission only — automated signals service stays off for canary 2)
- Max notional per order USDT: `25`
- Max leverage: `1`
- Max concurrent positions: `1`
- Max daily loss USDT: `5`
- Exercise window start UTC: `2026-05-05T12:00:00Z`
- Exercise window end UTC: `2026-05-05T12:30:00Z`
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
    max_notional_usd: 25
    max_leverage: 1
    max_daily_loss_usd: 5
    max_concurrent_positions: 1
    require_stop_loss: true
    require_take_profit: true
    exercise_window_start: "2026-05-05T12:00:00Z"
    exercise_window_end:   "2026-05-05T12:30:00Z"
    auto_cancel_flatten_after: true
```

## Rationale for these limits

Same caps as canary 1 retry (commit `31e9340`): ETHUSDT-PERP at $25
notional with $5 daily-loss cap, 1× leverage, single concurrent
long position. After Bug 1 (commit `a16eb66`) symbol-filter fix,
$25 satisfies real ETHUSDT mainnet `min_notional=20` with
`step_size=0.001` round-down buffer.

Subsequent canaries (after this one closes cleanly) can scale
**one** parameter at a time, never multiple, with a fresh
authorization commit. Path C accelerated plan
(`/Users/darkagent001/.claude/plans/lucky-foraging-karp.md`)
sketches the next 6 weeks.

## Signature

- Operator: `darkagent001`
- Date: `2026-05-05`
- Notes: `Canary 2 (12:00-12:30 UTC = 20:00-20:30 local UTC+8) — second canary, same caps as 31e9340 retry. Bug 1 (a16eb66) and Bug 3 (07a4800 cleanup grace) fixes verified by 414/414 unit tests at 11:45 UTC. Path C accelerated plan approved; this is week 1 day 1.`
