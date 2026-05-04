# Canary 1 — 2026-05-04T04:30:00Z to T05:00:00Z

First Gate 6 mainnet micro-live canary. **Result: zero real-money
exposure, zero PnL, two genuine codebase bugs found and one fixed.**

This addendum is the operator's primary record. The incident report
covers the closeout-blocked bug specifically.

## Authorization

- Window: 2026-05-04T04:30:00Z → 2026-05-04T05:00:00Z (30 min)
- Symbol: ETHUSDT-PERP
- Direction: long only
- Notional cap: 25 USDT (bumped from initial 10 after live ETHUSDT
  min_notional discovery — see §Bug 2)
- Leverage: 1×
- Daily loss cap: 5 USDT
- Concurrent positions: 1
- Authorization commits: `efefe31` (initial 04:00-04:30 / $10 cap),
  `31e9340` (retry 04:30-05:00 / $25 cap after bug fix).
- Funding: 100 USDT in mainnet Futures wallet (>> required 35 USDT).

## Submitted ticket

| Field | Value |
|---|---|
| ticket_id | `01KQRM0Y8ARV47PBZTT1FBNJXG` |
| dispatch_ref | `live:01KQRM0Y8ARV47PBZTT1FBNJXG` |
| mark price | 2390.90 USDT |
| entry (LIMIT BUY) | 2378.94 (mark × 0.995) |
| stop loss | 2355.15 (entry × 0.99) |
| take profit | 2402.73 (entry × 1.01) |
| quantity | 0.010 ETH |
| notional | 23.79 USDT |
| orders dispatched | 3 (entry + stop + tp) |
| accepted by Binance | yes (3/3) |
| filled | no — entry resting below market for 30 min |
| PnL | 0 USDT |

## Mid-window evidence

- 04:51:43Z manual `reconcile-live` returned `status=ok` with
  `live_order_sync.symbol count=3 symbol=ETHUSDT-PERP`. All three
  orders confirmed alive on Binance mainnet, local DB in sync.
- Throughout window: kill switch 🟢 clear; 0 halt events; circuit
  breakers all clear; 0 reject reasons on the new ticket.
- user-stream WebSocket: connected, listenKey created at 04:30:03Z,
  no `event_ingested` heartbeats (no fills to ingest in quiet
  ETHUSDT window).

## Bugs found

### Bug 1 — `_fetch_symbol_filters` returned `symbols[0]` (BTCUSDT) for any non-BTCUSDT request

- Source: `src/execution/exchange_filters.py:75–90` (pre-fix).
- Surface: Gate 6 first canary attempt at 04:00 UTC raised
  `ExchangeFilterError: notional_below_min_notional:BTCUSDT:9.4976<50`
  on a `--symbol ETHUSDT-PERP` submission. The 50 USDT min was
  BTCUSDT's, served because `/fapi/v1/exchangeInfo` silently ignores
  the `?symbol=` query parameter and returns all 716 mainnet symbols
  with BTCUSDT first. Months of testnet exercise masked the bug —
  testnet returns ~30 symbols with BTCUSDT first too, and most prior
  testnet canaries used BTCUSDT.
- Caught fail-closed by `assert_min_notional` before any broker call;
  zero mainnet exposure.
- Fix: commit `a16eb66` `fix(exchange-filters): pick requested symbol
  from /fapi/v1/exchangeInfo`. Two regression tests added.
- Verified live against mainnet post-fix: ETHUSDT now returns
  `tick=0.01 step=0.001 min_qty=0.001 min_notional=20`; BTCUSDT
  returns `tick=0.10 step=0.001 min_qty=0.001 min_notional=50`.

### Bug 2 — ETHUSDT mainnet min_notional discovered at 20 USDT (not 5 as documented)

- Source: docs/gate-6-micro-live-runbook.md and
  docs/gate-6-authorization.md template both used $10 max_notional
  reflecting an outdated assumption that ETHUSDT min_notional was
  ~5 USDT.
- Surface: even after Bug 1 was fixed, the $10 cap could not satisfy
  the real $20 min_notional. Recomputed:
  $25 cap → qty 0.01 (rounded down from 0.0105 by step 0.001) →
  notional 23.79 → above min_notional 20 ✓
- Fix: authorization re-commit `31e9340` bumps cap to $25.
- Action item for runbook update (separate commit): refresh
  docs/gate-6-micro-live-runbook.md and the
  authorization-template caps to $25 / ETHUSDT.

### Bug 3 — `gate6 closeout` blocked by `mainnet_outside_exercise_window` after window end

- Source: `_assert_in_exercise_window` in
  `src/execution/live_safety.py:213` is reached by every
  `assert_live_mode_enabled` caller, including the cleanup paths.
- Surface: at T+30 (05:01Z) `gate6 closeout` failed with
  `✗ gate6 closeout blocked: mainnet_outside_exercise_window`.
  Three resting orders still on mainnet; CLI cleanup path closed.
- Mitigation taken: operator manually cancelled via Binance Futures
  UI within ~1 min. Read-only signed GET at 05:03:19Z confirmed
  `openOrders=0`, `positionAmt=0`. Zero unattended fill risk.
- Fix: deferred — see `docs/incidents/2026-05-04-canary-1-closeout-window-gate.md`.
  Recommend `[start, end + grace_minutes]` window for cleanup paths.
  Tracked in `docs/audit-followups.md`.

## What this canary proved

- ✅ Authorization paperwork end-to-end (sign / commit / push)
- ✅ main.yaml mainnet flip + revert
- ✅ user-stream `mainnet_outside_exercise_window` window-gate (start)
- ✅ submit-canary `mainnet_outside_exercise_window` window-gate (start)
- ✅ submit-canary 90s heartbeat freshness check
- ✅ exchange-info filter resolves correct symbol (post-fix)
- ✅ broker dispatches full bracket (entry + stop + TP) to Binance
  mainnet and Binance accepts all three
- ✅ mid-window `reconcile-live` returns `ok` with correct order count
- ✅ kill switch never trips during quiet idle window
- ✅ user-stream persists connection through 30-min idle
- ✅ Read-only signed REST GETs work for verification
- ⚠️ `gate6 closeout` post-window blocked (Bug 3)

## What this canary did NOT prove (left to future canaries)

- entry-fill → bracket-activation → stop/TP fill chain on real fills
- emergency_close path on a real position
- multi-symbol behaviour
- closeout writing `reports/gate6-closeout-*.md`

## Operator state at end of window

- main.yaml reverted to `live/testnet` `allow_mainnet=false`
  `micro_live.enabled=false`. Working tree clean for
  `config/main.yaml`.
- Local DB: ticket `01KQRM0Y8ARV47PBZTT1FBNJXG` left as `accepted`
  (historical record; no position row, no incoming fill events
  expected). Future reconciles will not flag because positions table
  is empty and the ticket has no associated open order on exchange.
- Binance mainnet: 0 open orders, 0 position, balance unchanged minus
  exchange-fee accruals (none — orders never fully placed/filled).
- user-stream and dashboard still running; recommend operator stops
  user-stream now (Ctrl+C) and either keeps dashboard up for ongoing
  observability or shuts it down with `lsof -ti :8766 | xargs kill`.

## Next canary

Canary 2 prerequisites:

1. **Bug 3 fix landed and tested** — `_assert_in_exercise_window`
   must allow closeout for at least 5 minutes past `end`. Ship in
   its own commit before scheduling the next canary.
2. Optional: refresh runbook with updated $25 / ETHUSDT defaults so
   the authorization template no longer requires manual override.
3. Pick a fresh quiet window (Sunday 02-06 UTC, or another
   Sun→Mon UTC handover slot).
4. Re-issue authorization with the chosen window timestamps.
5. Same observation tooling sequence: dashboard up → main.yaml flip
   → at T-0 start user-stream → wait for `connected` heartbeat → at
   T+0 + ~30s submit-canary.
