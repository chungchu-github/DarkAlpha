# Gate 6 Mainnet Micro-Live Authorization

- Generated at UTC: `2026-04-26T14:12:17.319570+00:00`
- Operator: `darkagent001`

## Operator Limits

- Authorized symbol: `BTCUSDT-PERP`
- Direction allowed: `long`
- Strategy allowed: `manual_test_signal`
- Max notional per order USDT: `100`
- Max leverage: `1`
- Max concurrent positions: `1`
- Max daily loss USDT: `5`
- Exercise window start UTC: `2026-04-26T14:10:00Z`
- Exercise window end UTC: `2026-04-26T14:40:00Z`
- Auto cancel-all after window: `yes`
- Auto flatten after window: `yes`

## Safety Acknowledgement

- [x] Mainnet key is dedicated to this bot.
- [x] Mainnet key has no withdrawal permission.
- [x] Mainnet key is IP restricted when possible.
- [ ] `poetry run dark-alpha gate-check all` passed immediately before the run.
- [ ] Binance account has no unknown open orders.
- [ ] Binance account has no unknown position.
- [x] Every live ticket must include stop loss and take profit.
- [x] Operator accepts that this is a micro-live canary, not production live trading.

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
      - BTCUSDT-PERP
    max_notional_usd: 100
    max_leverage: 1
    max_daily_loss_usd: 5
    max_concurrent_positions: 1
    require_stop_loss: true
    require_take_profit: true
    exercise_window_start: "2026-04-26T14:10:00Z"
    exercise_window_end: "2026-04-26T14:40:00Z"
    auto_cancel_flatten_after: true
```

## Signature

- Operator: `darkagent001`
- Date: `2026-04-26`
