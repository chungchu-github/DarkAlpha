# Gate 6 Mainnet Micro-Live Authorization

> **Draft state.** This file is the proposed parameter set for the
> first canary, prepared after Gate 6.7 burn-in 3/3 cleared
> (commit `9c76eec`). The exercise window timestamps and signature
> are **placeholders** — operator must fill them with the actual
> chosen window and re-sign immediately before the canary, then
> commit. See `docs/first-canary-checklist.md` for the launch
> sequence.

- Generated at UTC: `2026-05-04T03:40:00Z`
- Operator: `darkagent001`

## Operator Limits

- Authorized symbol: `ETHUSDT-PERP`
- Direction allowed: `long`
- Strategy allowed: `manual_test_signal` (Gate 6 canary submission only — automated signals service stays off for first canary)
- Max notional per order USDT: `10`
- Max leverage: `1`
- Max concurrent positions: `1`
- Max daily loss USDT: `5`
- Exercise window start UTC: `2026-05-04T04:00:00Z`
- Exercise window end UTC: `2026-05-04T04:30:00Z`
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
    max_notional_usd: 10
    max_leverage: 1
    max_daily_loss_usd: 5
    max_concurrent_positions: 1
    require_stop_loss: true
    require_take_profit: true
    exercise_window_start: "2026-05-04T04:00:00Z"
    exercise_window_end:   "2026-05-04T04:30:00Z"
    auto_cancel_flatten_after: true
```

## Rationale for these limits

These mirror the existing design intent in
`docs/gate-6-micro-live-runbook.md` (notional 10, leverage 1, daily
loss 5) rather than the pre-burn-in placeholder of $100 / BTCUSDT.
The lower notional + ETH choice means:

- ~$10 maximum per-order capital exposure (Binance Futures ETHUSDT
  min notional is far below this; safe headroom).
- ~$0.10 expected loss if a 1% stop fires; $1 if the stop is delayed
  by 10%.
- $5 daily cap = ~5 stop-outs of headroom before the breaker halts
  trading for the day.

Subsequent canaries (after the first one closes cleanly) can scale
**one** parameter at a time, never multiple, with a fresh
authorization commit.

## Signature

- Operator: `darkagent001`
- Date: `2026-05-04`
- Notes: `Dashboard 2h soak 2026-05-03T14:57:38Z PASS (commit 5f2f7e4); 3/3 burn-in chain met (9c76eec). 04:00-04:30 UTC = Sun→Mon handover quiet slot.`
