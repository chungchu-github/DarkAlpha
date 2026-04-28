# Incident — Bracket-reject orphan position survives 24h burn-in

- **Detected by**: first 24h Gate 6.7 burn-in run (`docs/burn-in-2026-04-26T164808Z/`)
- **Severity**: P0 (would be a real money loss exposure on mainnet)
- **Affected component**: `LiveEventGuard` wiring — only attached to user-stream
  WebSocket path, missing on REST/reconcile path
- **Status when written**: orphan ticket still on Binance testnet, kept as
  reproduction fixture for fix verification

## Time line (all UTC)

| Timestamp | Event |
|---|---|
| 2026-04-26 06:24:45 | Manual Gate 2 test ticket `01KQ47CFJB18G3J9A3PE3EZWFR` submitted by an earlier (pre-`1b44e72`) build of `BinanceFuturesBroker`. Entry limit BUY 0.001 BTC @ 80000 accepted by Binance; **stop_market and take_profit_market rejected with -4120** ("Order type not supported for this endpoint. Please use the Algo Order API endpoints instead"). The pre-fix broker did not cancel the entry on bracket reject. |
| 2026-04-26 15:47:19 | Commit `1b44e72` lands. Broker now routes `stop`/`take_profit` to `new_algo_order`, and wraps `submit_ticket` with a try/except cancel-sweep on any partial bracket failure. **The orphan entry is still resting on the exchange**; routing fix is post-hoc and cannot reach a limit order that was already accepted before the fix. |
| 2026-04-26 16:48:08 | First 24h burn-in starts (`live/testnet`). `dark-alpha doctor` only checks schema completeness — does not detect open positions / submitted orders / pending tickets in DB. `_assert_no_open_orders` would catch it but is only called inside `submit_ticket`, not at startup. |
| 2026-04-26 16:48:13 | Market drifts to 80000, Binance fills the resting limit entry. Local DB is updated by `LiveOrderStatusSync.sync_symbol` (REST polling inside reconcile). A `positions` row is created with `shadow_mode=0`, `status='open'`, `stop_price=30000`, `take_profit_price=180000` (the original ticket's nominal levels — the actual stop / take_profit orders are still `status='rejected'` in `order_idempotency`). **No live_stream_event row was recorded for this fill** — user-stream WebSocket had not finished its listenKey + WS handshake within the 5-second window since burn-in started. |
| 2026-04-26 16:48 → 2026-04-27 16:48 | 24 hourly snapshots all report `kill switch 🟢 clear` and `reconcile-live status=ok`. Gate 6 readiness reports `gate6.6: open positions protected = fail (BTCUSDT)` every snapshot but readiness is a passive observation — it does not activate the kill switch. The orphan rests unprotected for 24 hours. |
| 2026-04-27 22:56 / 23:31 | Two `strategy.dispatch_failed error=user_stream_unhealthy` events in the receiver log. These are the safety chain *correctly* refusing to dispatch new tickets when user-stream heartbeat is missing — unrelated to the orphan, included here only because they appeared in the post-burn-in error grep. |

## Why the safety chain missed it

`LiveEventGuard.inspect_ticket_after_fill` (`src/execution/live_event_guard.py:47`)
exists and works. It detects a position whose `order_idempotency` rows for
`stop`/`take_profit` are not in `{submitted, acknowledged}`, and fires the kill
switch with `live_position_missing_protective_orders:...`.

But it is wired in **exactly one place**:
`LiveUserStreamIngestor.process_event` calls `inspect_ticket_after_fill` after
each known-order fill event (`src/execution/live_user_stream.py:197`).

Two paths can land a fill into local state:

1. **WebSocket path** — `ORDER_TRADE_UPDATE` event ingested by user-stream.
   This path *does* invoke the guard.
2. **REST path** — `LiveOrderStatusSync.sync_symbol` polls Binance and
   reconciles local order/position state. Called from
   `LiveReconciler._reconcile_symbol`, also from the periodic reconcile-live
   command and from burn-in's hourly snapshot loop. **This path does not
   invoke the guard.**

When the entry filled at 16:48:13, user-stream had not yet connected, so the
fill was discovered minutes later by the next reconcile sweep via REST. The
guard never ran. Reconcile's own mismatch detection compares local↔exchange
order/position counts and amounts — those matched (entry filled, no stops
present locally and none on exchange) — so it returned `status=ok`.

## Resolution

A single targeted change closes the gap:
**`LiveReconciler.run` invokes `LiveEventGuard.inspect_all_active_positions`
after the per-symbol sync loop and before status finalization.** Any open live
position whose protective bracket is not active becomes an explicit reconcile
mismatch and activates the kill switch.

The guard logic itself is unchanged. The broker routing fix that landed in
`1b44e72` is unchanged. The user-stream guard hook remains as the primary
event-driven path; reconcile-time inspection is the safety net for any path
that bypasses user-stream (REST polling, missed events, listenKey expiry,
crashed user-stream process).

A second change adds a clean-state pre-flight to `scripts/burn-in.sh`: refuse
to start if the DB has open live positions, in-flight orders, or unfinished
tickets. This prevents *any* dirty pre-existing state from being inherited by
a burn-in window. `dark-alpha doctor` is unchanged because its scope is
schema/config integrity, not operational state.

## What is intentionally NOT changed

- **Broker code** (`binance_testnet_broker.py`) — routing was already fixed in
  `1b44e72` and there is already a unit test asserting `stop_market` /
  `take_profit_market` go through `new_algo_order`.
- **User-stream guard hook** — already correct.
- **Position manager / order sync** — already correct in their own scope.
  The fix is at the reconcile layer specifically because that is the layer
  whose job is to be the system-wide consistency check.
- **The testnet orphan position itself** — left in place as a reproduction
  fixture. After this incident's fix lands, manually placing matching stop +
  take_profit algo orders for ticket `01KQ47CFJB18G3J9A3PE3EZWFR` should make
  reconcile clear, and the position can then be closed via `emergency-close`.

## Verification

The first 24h burn-in is filed under `docs/burn-in-2026-04-26T164808Z/` with
an `INVALID.md` marker referencing this incident. The Gate 6.8 three-clean
counter resets to zero — re-running the first burn-in is gated on the fix
landing and the testnet orphan being closed.
