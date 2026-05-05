# Canary 4 — 2026-05-05T14:00:00Z to T15:00:00Z

Fourth Gate 6 mainnet micro-live canary. **Path C 3-gate Gate A
real-fill milestone HIT** — first canary across 1+2+3+4 to actually
reach entry fill on mainnet. Initial $100 dispatch rejected by
Binance margin check (-2019), retried at $80 and entry filled at
2382.38. **user-stream missed all events** (Bug 5: zombie process
accumulation); reconcile-via-closeout auto-detected the orphan
position and flattened. **DB exit_price / net_pnl not populated**
(Bug 6: closeout flatten doesn't write exit fields to position row).

## Authorization

- Window: 2026-05-05T14:00:00Z → 2026-05-05T15:00:00Z (60 min,
  doubled vs canary 1-3's 30 min)
- Symbol: ETHUSDT-PERP
- Direction: long
- Notional cap: 100 USDT in authorization, **runtime-reduced to 80
  USDT** after margin -2019 (see §Bug 7 funding boundary)
- Entry offset: **0.1%** (CLI `--entry-offset-pct 0.001`, half of
  canary 3)
- Leverage: 1×
- Daily loss cap: 5 USDT
- Concurrent positions: 1
- Authorization commit: `da47b23`
- Bug 4 fix in HEAD: `c8985b8`

## Submitted ticket

| Field | Value |
|---|---|
| ticket_id | `01KQW737SXYGZCABMP8CYBKM1Q` |
| dispatched at (after retry) | 2026-05-05T14:01:34Z (T+1:34) |
| mark price | 2384.77 USDT |
| entry (LIMIT BUY) | 2382.38 (mark × 0.999) |
| stop loss | 2358.56 (entry × 0.99) |
| take profit | 2406.20 (entry × 1.01) |
| quantity | 0.033 ETH (step 0.001) |
| notional | 78.6185 USDT |
| orders dispatched | 3 (entry + stop + tp) |
| accepted by Binance | yes (3/3) |
| **filled** | **YES — entry filled @ 2382.38, 0.033 ETH** |
| flatten exit | MARKET SELL @ 2377.59, 0.033 ETH (Binance order 8389766172243536689) |
| **gross PnL (realized)** | **-$0.15807 USDT** (verified via Binance UI screenshot) |
| flatten fee | 0.00005599 BNB (~$0.025 USDT equivalent) |
| net PnL after BNB fees | ≈ -$0.183 USDT |

## Closeout

| Field | Value |
|---|---|
| closeout ran at | 2026-05-05T14:59:11Z (T+59:11, inside window) |
| flatten submitted | **True** (because reconcile detected open position) |
| reconciliation | ok |
| Run ID | `01KQWAD1168VGSEDD75NERE1W3` |
| local flat repair | 1 (one position closed locally) |
| report file | `reports/gate6-closeout-ETHUSDT-20260505T145917Z.md` |

Closeout sync table:
- DAENB**SX**YGZCABMP8CYBKM1Q (entry): **FILLED on exchange, 0.033 @ 2382.38**
- DASTS**SX**YGZCABMP8CYBKM1Q (stop): CANCELED (cancel-all reaped before SL fired)
- DATPS**SX**YGZCABMP8CYBKM1Q (TP): CANCELED (cancel-all reaped before TP fired)
- DAENB**X016**GRMY5477Z2FESH (failed-attempt entry): NOT_FOUND on exchange
- DASTS**X016**GRMY5477Z2FESH: NOT_FOUND
- DATPS**X016**GRMY5477Z2FESH: NOT_FOUND

The `X016GRMY5477Z2FESH` orders are from the failed $100 attempt
(rejected by margin -2019, never reached exchange). The
`SXYGZCABMP8CYBKM1Q` orders are from the successful $80 retry.

## Three new bugs discovered

### Bug 5 — Zombie user-stream processes accumulate; pkill misses Python child

- **Surface**: at end of canary 4 cleanup, `pgrep -fa user-stream listen`
  showed **4 zombie Python processes** (PIDs 2864 / 9202 / 96180 /
  96687) with elapsed times 60-180 min, indicating accumulation
  across canary 2 / 3 / 4 + one earlier session.
- **Root cause**: previous cleanups used
  `pkill -f "user-stream listen"` — this matches the **bash wrapper**
  that invoked `poetry run dark-alpha user-stream listen`. Killing
  the wrapper does NOT kill the spawned Python child (different
  process tree under poetry's exec model). Each canary spawned a
  new wrapper + Python pair; the old Python keeps running with its
  own listenKey.
- **Impact**: each zombie holds an active Binance listenKey and
  WebSocket connection. When Binance dispatches an
  `ORDER_TRADE_UPDATE` for our account, it picks ONE listenKey to
  push to — the others get nothing. Across 4 zombies, our "current"
  user-stream had a 25% chance of receiving any given event.
- **Evidence in canary 4**:
  - `live_stream_events` table: **0 rows for 2026-05-05** — none of
    the 4 user-streams ingested ANY event today.
  - `live_runtime_heartbeats`: 245 `alive` heartbeats from canary
    4's user-stream alone (Bug 4 fix working) — proving the
    "current" stream WAS connected, just got 0 events.
  - The fill itself was discovered at T+59:11 when closeout's
    `live_order_sync` polled exchange state directly (not via
    stream).
- **Mitigation now**: `kill -9 <PID>` against each zombie PID
  worked. All 5 dead at end of cleanup.
- **Permanent fix needed**: launch user-stream via `exec` so the
  Python process IS the foreground, not a child. OR add a
  PID-file mechanism so we can find and kill the right process.
  Track in audit-followups.

### Bug 6 — Closeout flatten path doesn't populate exit_price / net_pnl on position row

- **Surface**: after closeout's reconcile detected the orphan
  position and ran flatten, the `positions` row for canary 4
  shows:
  ```
  status=closed
  exit_reason=manual_flatten_reconciled
  exit_price=""           ← MISSING
  gross_pnl_usd=NULL      ← MISSING
  fees_usd=NULL           ← MISSING
  net_pnl_usd=0.0         ← WRONG (we did exit, just not @ 0.0)
  ```
- **Root cause hypothesis**: the flatten path that triggers via
  reconcile-discovered-orphan ("local_flat_repair") marks status
  closed but doesn't query the actual flatten fill from the
  exchange to populate exit fields. The user-stream-driven path
  (when working) does populate these from `ORDER_TRADE_UPDATE`.
- **Impact**: any post-canary PnL accounting that reads the
  positions table sees `net_pnl=0` and reports break-even, when
  in reality there is a real (but small) realized PnL on the
  exchange. Daily-loss cap tracking would also be wrong. **This
  is a Path C blocker**: production cannot rely on local PnL
  state if reconcile-flatten paths are silent on outcome.
- **Mitigation now**: operator must check Binance UI / Trade
  History to determine actual realized PnL for canary 4.
- **Permanent fix needed**: in the local_flat_repair path, query
  exchange trade history for the flatten fill and back-fill
  exit_price + fees + gross/net PnL into the position row.
  Track in audit-followups.

### Bug 7 — Account funding boundary at $100; -$0.x of fees breaks $100 cap

- **Surface**: $100 notional dispatch at 14:00:33Z rejected with
  `-2019 Margin is insufficient`. Account had ~$100 USDT (post
  canary 1+2+3 zero PnL). The $100 notional × 1× leverage
  required ~$100 initial margin + small maintenance margin
  reserve + estimated commission. Just barely over the wallet
  balance.
- **Mitigation now**: dispatch retried at $80 cap (config edit
  `max_notional_usd: 100 → 80`), succeeded.
- **Permanent fix needed**: pre-dispatch wallet-balance check in
  `submit_gate6_canary` that compares
  `required_margin_with_buffer = notional × 1.05 / leverage`
  against current wallet balance, and refuses with a clear
  error if insufficient. Currently the check happens server-side
  (Binance returns -2019), wasting the dispatch attempt and
  half-corrupting local DB state with `reserved` orders that
  show as `rejected` later.
- **Operator action**: add ≥$50 USDT to mainnet Futures wallet
  before next canary (target: $200 to comfortably handle $100
  notional + fees + buffer for canaries 5+).

## Path C Gate A status update

| Component | Status | Evidence |
|---|---|---|
| $100 scale-up | ✅ proven (canary 3) | dispatch chain clean at 4× notional |
| Bug 4 fix (alive heartbeat) | ✅ proven (canary 3+4) | 57 + 245 alive heartbeats fired across windows |
| **real entry fill** | **✅ HIT (canary 4)** | DAENBSXYGZCABMP8CYBKM1Q FILLED 0.033 @ 2382.38 |
| **bracket SL/TP fire on fill** | ❌ unproven (cancel-all reaped first) | stop + TP cancelled by closeout, never fired |
| **user-stream catches fill events** | **❌ FAILED (canary 4)** | 0 stream events ingested all day |
| **closeout flatten path PnL accounting** | **❌ FAILED (canary 4)** | exit_price empty, net_pnl=0 in DB |
| short direction | ❌ unproven |  |
| concurrent positions | ❌ unproven |  |

**Net Gate A progress:** 3/8 components proven (one of which —
real fill — is the headline milestone we'd been chasing for 4
canaries). 3 NEW BUGS surfaced that block clean Gate B
progression: zombie cleanup, flatten PnL accounting, funding
buffer enforcement.

## What this canary did NOT prove

- **Bracket SL/TP firing on a real fill.** The entry filled, but
  before the market could move to either bracket leg, closeout's
  cancel-all reaped both stop and TP. So we never observed:
  - SL trigger logic on real position
  - TP trigger logic on real position
  - exit_reason=stop_loss or =take_profit (only saw
    =manual_flatten_reconciled)
  - PnL accounting on a "natural" close (only forced flatten)
- **short direction**, **concurrent positions** — explicitly held
  for canary 5+.

## Path C decision point

**Cannot proceed to Gate B (24/7 daemon) until Bug 5 + Bug 6 fixed.**
Reasons:
- Bug 5 means in 24/7 mode, restart cycles will accumulate zombies
  faster than current pace. By day 3 we'd have 50+ stale
  listenKeys, 95%+ event-loss rate.
- Bug 6 means daily-loss tracker (which reads `positions.net_pnl`)
  would be wrong, and the safety mechanism designed to halt
  trading at -$5/day would not see real losses.

**Suggested order:**
1. **Fix Bug 5** (PID-file or `exec` user-stream launch) — ~30 min
2. **Fix Bug 6** (back-fill exit fields after local_flat_repair) —
   ~1 hr (need to query Binance trade history API)
3. **Optional: Fix Bug 7** (pre-dispatch wallet-balance check) —
   ~30 min (or accept operator-managed funding for now)
4. **Operator**: add USDT to wallet for canary 5 ($200 target)
5. **Canary 5**: replicate canary 4 ($80 long, 60min, entry 0.1%)
   with all bugs fixed + watch user-stream actually catch the
   fill event this time. Goal: prove SL or TP fires natively
   (not just dispatch+flatten chain).

## Operator state at end of window

- main.yaml reverted to `live/testnet` `allow_mainnet=false`
  `enabled=false` `max_notional=25` `windows=""`. Working tree
  clean for `config/main.yaml`.
- Local DB ticket `01KQW737SXYGZCABMP8CYBKM1Q` = `accepted`,
  position `01KQWACXRSXXDAPT8HX3FEEP2V` = `closed`,
  exit_reason=`manual_flatten_reconciled`, **exit_price + PnL
  fields BLANK** (Bug 6).
- Binance mainnet: 0 open orders, 0 position (verified by 14:59
  reconcile + 15:01 reconcile). Account balance changed by
  realized PnL of canary 4 entry fill + flatten — magnitude
  unknown until operator queries Binance UI.
- 4 zombie user-stream Python processes (PIDs 2864 / 9202 /
  96180 / 96687) killed via direct `kill -9 <PID>` after
  `pkill -f` proved insufficient. 0 user-stream processes left.
- Dashboard left running on port 8766.

## Operator follow-ups (TODO before canary 5)

1. **Check Binance UI Trade History** for canary 4's flatten
   trade and report actual realized PnL. ✅ **DONE
   2026-05-05T~15:00Z** — flatten was MARKET SELL @ 2377.59,
   0.033 ETH; realized PnL = **-$0.15807 USDT** + 0.00005599
   BNB fees (~$0.025). Bug 6 fix's `gross_pnl` formula
   `(exit - entry) × qty` was independently verified against
   Binance's `已實現盈虧` field — **bit-for-bit match at
   -$0.15807** at 5 decimal places.
2. **Top up mainnet Futures wallet to ≥$200** before next canary.
   ✅ **DONE** — operator confirmed wallet at $200 USDT after
   canary 4 closeout.
3. **Wait for Bug 5 + 6 fixes** to land before canary 5.
   ✅ **DONE** — commit `786d28c` (Bug 5: PID file +
   `user-stream stop`) and commit `50fe87a` (Bug 6: backfill
   exit_price + PnL on closeout flatten path). 425/425 unit
   tests pass.
