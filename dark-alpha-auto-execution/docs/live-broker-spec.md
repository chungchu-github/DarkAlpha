# Live Broker Safety Spec

Status: Gate 2 testnet adapter exists. Gate 2.5 -> Gate 6 executable
checks now exist. Mainnet remains disabled by default.

## Scope

The live broker may only be implemented after Gate 2 authorization. It must not
replace the existing shadow broker path. The live path must satisfy every item
below before a real Binance signed request can be sent.

## Required Controls

- Deterministic `clientOrderId` per ticket/order role.
- Idempotency reservation before submit.
- Testnet/mainnet explicit environment separation.
- Mainnet requires `allow_mainnet=true`, a signed authorization file, dedicated
  mainnet credentials, explicit micro-live caps, symbol allowlist, and an active
  exercise window.
- Entry order, stop order, and take-profit order must be planned together.
- Stop and take-profit orders must be `reduceOnly`.
- No duplicate symbol positions unless risk config explicitly allows it.
- Reconciliation after submit, after fill, and on startup.
- Cancel-all command for the configured symbol universe.
- Emergency-close command for open live positions.
- API timeout and rejected-order paths must fail closed.
- Rate-limit handling must not retry by creating new client order IDs.

## Client Order ID Format

`DA{role}{side}{ticket_suffix}`

Examples:

- `DAENB01HF...` for entry buy
- `DASTS01HF...` for stop sell
- `DATPS01HF...` for take-profit sell

The ID must be deterministic so reprocessing the same ticket cannot submit a
second logically identical order.

## Reconciliation Contract

Startup reconciliation must compare:

- local `execution_tickets`
- local `positions`
- local `order_idempotency`
- exchange open orders
- exchange positions

Any mismatch must trip a halt action before new entries are allowed.

## Implemented In Phase 5 / Gate 2.5 Start

- Binance Futures testnet signed REST client.
- Testnet compatibility broker adapter.
- Generic Binance Futures broker scaffold for testnet/mainnet, with mainnet
  gated by `live_safety`.
- Entry + stop + take-profit bracket submission payloads.
- Deterministic client order ids for planned ticket orders.
- Pre-submit checks for existing exchange position and open orders.
- Fail-closed cancel-all if bracket submission partially fails.
- Router connection from `shadow_mode=false` ticket to testnet broker after Gate 2 preflight.
- Testnet `cancel-open-orders` CLI command.
- Testnet `flatten --symbol` reduce-only market emergency close command.
- Startup reconciliation against local DB, exchange open orders, and exchange positions.
- Reconciliation mismatch activates the kill switch and therefore the existing critical alert path.
- Order status polling updates local `orders` and `order_idempotency`.
- Entry fills create/update live `positions`.
- Entry partial fills set live `positions.status='partial'`.
- Entry full fills set live `positions.status='open'`.
- Stop fills close live positions with `exit_reason='stop_loss'`.
- Take-profit fills close live positions with `exit_reason='take_profit'`.
- Paper evaluator ignores live positions.
- Deterministic local Gate 2.5 fill-lifecycle check:
  `dark-alpha gate-check gate25`.
- Deterministic Gate 3 restart/reconciliation/kill-switch check:
  `dark-alpha gate-check gate3`.
- Deterministic Gate 3.5 risk rejection matrix:
  `dark-alpha gate-check gate35`.
- Deterministic Gate 5 and Gate 6 scaffold checks:
  `dark-alpha gate-check gate5` and `dark-alpha gate-check gate6`.

## Still Not Implemented

- Real mainnet micro-live canary execution.
- User-data WebSocket order stream; current live status sync remains polling.
- Fully automated post-window mainnet cancel-all + flatten orchestration.

Mainnet must stay blocked until the operator explicitly configures
`live.micro_live`, supplies dedicated mainnet credentials, opens a short
exercise window, runs `dark-alpha gate-check all`, and accepts Gate 6 canary
risk in writing.
