# Gate 6 Mainnet Micro-Live Authorization

> **Canary 5** — fifth micro-live canary, **Path C 3-gate Gate A
> Bug 5+6 fix verification**. Same caps as canary 4 ($80 long
> 60min entry 0.1%) — the goal is to prove that:
> 1. Bug 5 fix prevents zombie user-stream accumulation
> 2. Bug 6 fix backfills exit_price + PnL on the position row
> 3. Live user-stream actually catches ORDER_TRADE_UPDATE events
>    this time (live_stream_events table populated, not 0 rows)
> 4. Position lifecycle works through stream events (not via
>    closeout reconcile-discovery)

- Generated at UTC: `2026-05-05T15:59:30Z`
- Operator: `darkagent001`

## Operator Limits

- Authorized symbol: `ETHUSDT-PERP`
- Direction allowed: `long`
- Strategy allowed: `manual_test_signal`
- Max notional per order USDT: `80`
- Max leverage: `1`
- Max concurrent positions: `1`
- Max daily loss USDT: `5`
- Exercise window start UTC: `2026-05-05T16:00:00Z`
- Exercise window end UTC: `2026-05-05T17:00:00Z`
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
- [x] Dashboard 1–2h soak test passed.

## Matching `config/main.yaml` Block

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
    max_notional_usd: 80
    max_leverage: 1
    max_daily_loss_usd: 5
    max_concurrent_positions: 1
    require_stop_loss: true
    require_take_profit: true
    exercise_window_start: "2026-05-05T16:00:00Z"
    exercise_window_end:   "2026-05-05T17:00:00Z"
    auto_cancel_flatten_after: true
```

## Rationale for these limits

Canary 4 surfaced 3 bugs (5 / 6 / 7); canary 5 verifies fixes for
Bug 5 and Bug 6 in production. Same params as canary 4 to isolate
the bug-fix change as the only variable. Window 16:00-17:00 UTC
is operator-chosen; ack risks: funding settlement at exact 16:00
UTC may cause micro-volatility, and 00:00-01:00 local UTC+8 is
late-night operator alertness hit. Operator declined the safer
20:00 local recommendation.

## Signature

- Operator: `darkagent001`
- Date: `2026-05-05`
- Notes: `Canary 5 (16:00-17:00 UTC = 00:00-01:00 local UTC+8) — Path C 3-gate Gate A Bug 5+6 fix verification. Same params as canary 4. Wallet topped up to $200 (Bug 7 mitigated). Bug 5 commit 786d28c, Bug 6 commit 50fe87a. 425/425 unit tests pass.`
