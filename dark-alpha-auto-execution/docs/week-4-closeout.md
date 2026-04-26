# Week 4 Closeout

Status date: 2026-04-26

## Verdict

Week 4 is complete as a live-readiness phase.

Mainnet live trading remains disabled. The system has now moved into a Gate 2
testnet implementation phase, not mainnet execution.

## Completed

- Live broker safety specification exists.
- Gate 2 authorization template exists.
- Testnet/mainnet isolation config exists.
- Mainnet defaults to blocked.
- Live preflight blocks missing Gate 2 authorization.
- Deterministic `clientOrderId` generation exists.
- Order idempotency reservation table exists.
- Reconciliation run table exists.
- Router live path was fail-closed at Week 4 closeout.
- Shadow/paper path remains the default executable path.

## Phase 5 Update

Gate 2 testnet broker work has started:

- Binance Futures testnet signed REST client exists.
- Testnet broker can submit planned entry / stop / take-profit orders.
- Existing exchange positions and open orders block new entries.
- Partial bracket submission failure cancels all open orders for that symbol.
- Live order acknowledgements are written to local `orders`.
- `order_idempotency` rows are marked `submitted` after acknowledgement.
- `dark-alpha cancel-open-orders --symbol <SYMBOL>` exists for testnet.
- `dark-alpha flatten --symbol <SYMBOL>` exists for testnet reduce-only emergency close.
- `dark-alpha reconcile-live` exists for Gate 2 startup/manual reconciliation.
- `dark-alpha sync-live-orders` exists for manual testnet order polling.
- Supervisor runs live reconciliation once before live-mode ticks.
- Supervisor syncs live orders on live-mode ticks.
- Reconciliation mismatch activates the kill switch.
- Live entry fills create/update live positions.
- Live stop/take-profit fills close live positions.
- Paper evaluator ignores live positions.

## Current Live Path Behavior

For `shadow_mode=false` tickets:

1. Run live preflight.
2. Block if mainnet is not explicitly allowed.
3. Block if Gate 2 authorization file is missing.
4. Persist ticket only after preflight passes.
5. Reserve deterministic idempotency records.
6. Submit to Binance Futures testnet broker only if the global mode is `live`.
7. Record exchange acknowledgements locally.

This means accidental ticket-level live mode cannot silently submit orders while
the global config remains in shadow.

## Required Before Gate 2 Micro Live

- Produce 60-day shadow report.
- Review signal journal by strategy, symbol, and regime.
- Review backtest vs shadow report.
- Rotate Binance and Telegram credentials.
- Create environment-specific API keys.
- Run a real testnet dry exercise with user-provided testnet credentials.
- Verify cancel-all on testnet.
- Verify emergency-close on testnet.
- Verify reduce-only exits on testnet.
- Fill and sign `docs/gate-2-authorization.md`.

## Not Allowed Yet

- Mainnet live trading.
- Auto-flattening mainnet positions.
- Mainnet real order submission.
- Bypassing Gate 2 authorization.
- Reusing shadow-mode keys for live execution.

## Operator TODO

1. Revoke any API keys that have appeared in local `.env` files.
2. Create separate Binance testnet credentials.
3. Keep `config/main.yaml` in `mode: shadow`.
4. Run shadow mode long enough to produce a statistically useful report.
5. Use `dark-alpha report performance` weekly.
6. Use `dark-alpha report backtest-compare --csv-dir <dir>` when historical CSV is available.
