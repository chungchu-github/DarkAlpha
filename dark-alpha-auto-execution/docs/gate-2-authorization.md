# Gate 2 Authorization

Status: authorized for Binance Futures testnet only.

Date: 2026-04-26

## Scope

- Environment: testnet only
- Mainnet: not authorized
- Allowed mode: Gate 2 testnet broker validation
- Allowed symbols: BTCUSDT-PERP, ETHUSDT-PERP
- Required order shape: entry + stop + take-profit
- Required exits: reduce-only
- Required reconciliation: startup and manual reconciliation must pass
- Required emergency tools: cancel-open-orders and flatten

## Hard Limits

- Mainnet must remain `allow_mainnet: false`.
- `config/main.yaml` must remain `mode: shadow` until the operator intentionally starts a testnet exercise.
- Testnet exercise must use tiny quantities only.
- Any reconciliation mismatch must halt the system.
- Any unexpected exchange position must halt the system.
- Any unexpected exchange open order must halt the system.

## Operator Checklist Before Testnet Exercise

- Binance Futures testnet API key is configured.
- Binance Futures testnet API secret is configured.
- No mainnet API key is reused for this test.
- Kill switch path is known.
- Telegram alert path is configured or intentionally skipped for local dry run.
- `dark-alpha reconcile-live` passes before submitting new tickets.

## Explicit Non-Authorization

This file does not authorize mainnet trading.
