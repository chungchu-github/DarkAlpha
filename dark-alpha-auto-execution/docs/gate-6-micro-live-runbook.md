# Gate 6 Mainnet Micro-Live Runbook

Status date: 2026-05-04 (post-canary-1 update — defaults now reflect
real ETHUSDT mainnet min_notional of 20 USDT discovered during the
2026-05-04 canary)

## Verdict

Gate 6 is not normal live trading. It is a one-symbol, tiny-notional mainnet
canary. The default repository state must remain `mode: shadow`,
`live.environment: testnet`, and `live.allow_mainnet: false`.

## Required Config

In `config/main.yaml`, Gate 6 requires all of the following during the short
exercise window only:

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
    max_notional_usd: 25       # ETHUSDT mainnet min_notional = 20 USDT;
                               # 25 leaves room for step_size=0.001 round-down
    max_leverage: 1
    max_daily_loss_usd: 5
    max_concurrent_positions: 1
    require_stop_loss: true
    require_take_profit: true
    exercise_window_start: "2026-04-26T08:00:00+00:00"
    exercise_window_end: "2026-04-26T08:30:00+00:00"
    auto_cancel_flatten_after: true
```

Use the actual operator-approved values. Do not reuse testnet keys.

You can generate the authorization file and matching config block with:

```bash
poetry run dark-alpha gate6 authorize \
  --symbol ETHUSDT-PERP \
  --max-notional-usd 25 \
  --max-leverage 1 \
  --max-daily-loss-usd 5 \
  --window-start 2026-04-26T08:00:00+00:00 \
  --window-end 2026-04-26T08:30:00+00:00 \
  --strategy-scope manual_test_signal \
  --directions long \
  --operator <name>
```

## Required Environment

Set these in `/Users/darkagent001/DarkAlpha/.env`:

```text
BINANCE_FUTURES_MAINNET_API_KEY=...
BINANCE_FUTURES_MAINNET_API_SECRET=...
```

The key must have Futures trading only, no withdrawal permission, and should be
IP restricted.

## Mandatory Preflight

Run from `/Users/darkagent001/DarkAlpha/dark-alpha-auto-execution`:

```bash
poetry run dark-alpha gate-check all
poetry run dark-alpha status
poetry run dark-alpha gate6 preflight
```

Gate 6 cannot proceed if:

- kill switch is active
- `gate-check all` fails
- `main.yaml` is outside the approved exercise window
- mainnet credentials are missing
- `allowed_symbols`, notional cap, leverage cap, daily loss cap, or one-position
  cap are missing
- any ticket lacks stop loss or take profit
- Binance account has open regular orders, open algo orders, or non-zero
  position amount for the allowed symbol

## Exercise Flow

1. Confirm Binance account has no unknown position and no unknown open orders.
2. Run `poetry run dark-alpha gate6 preflight`.
3. Set the short Gate 6 window in `main.yaml`.
4. Start receiver and supervisor only inside that window.
5. Send exactly one approved micro-live signal or allow exactly one approved
   strategy trigger, according to the authorization document.
6. Immediately run:

```bash
poetry run dark-alpha sync-live-orders --symbol ETHUSDT-PERP
poetry run dark-alpha reconcile-live --symbol ETHUSDT-PERP
```

7. At the end of the window, run the closeout command. The CLI accepts a
   10-minute cleanup grace past `exercise_window_end`, so you have a
   reasonable buffer to land this command after the window closes:

```bash
poetry run dark-alpha gate6 closeout --symbol ETHUSDT-PERP --yes
```

The closeout command performs cancel-all, reduce-only flatten, order sync,
reconciliation, and writes a Markdown report under `reports/`.

If you wait longer than 10 minutes past `exercise_window_end`, the CLI
will refuse with `mainnet_outside_exercise_window` — the operator
fallback in that case is to manually cancel via the Binance Futures UI
and then `git checkout config/main.yaml` to revert to testnet. See
`docs/incidents/2026-05-04-canary-1-closeout-window-gate.md` for the
incident this grace was sized to handle.

Manual equivalent:

```bash
poetry run dark-alpha cancel-open-orders --symbol ETHUSDT-PERP --yes
poetry run dark-alpha flatten --symbol ETHUSDT-PERP --yes
poetry run dark-alpha sync-live-orders --symbol ETHUSDT-PERP
poetry run dark-alpha reconcile-live --symbol ETHUSDT-PERP
```

8. Restore:

```yaml
mode: shadow
live:
  environment: testnet
  allow_mainnet: false
  micro_live:
    enabled: false
```

## Hard Stop Conditions

Stop immediately and keep the kill switch active if any of these happen:

- exchange order exists without local idempotency row
- local open position differs from Binance position
- SL or TP is missing on exchange after entry fill
- partial fill cannot be reconciled
- API timeout occurs during uncertain order state
- any unexpected Binance rejection occurs

This runbook authorizes process only. It does not authorize mainnet trading by
itself.
