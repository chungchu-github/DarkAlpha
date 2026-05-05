# Canary 3 — 2026-05-05T13:00:00Z to T13:30:00Z

Third Gate 6 mainnet micro-live canary. **Path C 3-gate Gate A
(combined fill chain + scale-up).** Result: zero PnL, **Bug 4 fix
production-validated** (57 alive heartbeats, 30s interval, 0 gap),
$100 notional dispatch chain validated, **entry still no-fill at
0.2% (third consecutive no-fill across canary 1+2+3) — Gate A
core target (real fill chain) not yet hit.**

## Authorization

- Window: 2026-05-05T13:00:00Z → 2026-05-05T13:30:00Z (30 min)
- Symbol: ETHUSDT-PERP
- Direction: long only
- Notional cap: 100 USDT (4× canary 2's $25)
- Entry offset: **0.2%** (CLI `--entry-offset-pct 0.002`, default 0.5%)
- Leverage: 1×
- Daily loss cap: 5 USDT
- Concurrent positions: 1
- Authorization commit: `6d216d6`
- Bug 4 fix in HEAD: `c8985b8` (committed today 12:50 UTC)
- Pre-canary verification: 416/416 unit tests pass

## Submitted ticket

| Field | Value |
|---|---|
| ticket_id | `01KQW3KET5YAD9GM9880YY2ET4` |
| dispatch_ref | `live:01KQW3KET5YAD9GM9880YY2ET4` |
| dispatched at | 2026-05-05T13:00:33Z (T+0:33) |
| mark price | 2387.14 USDT |
| entry (LIMIT BUY) | 2382.36 (mark × 0.998 — **tighter than canary 1+2's 0.995**) |
| stop loss | 2358.54 (entry × 0.99) |
| take profit | 2406.18 (entry × 1.01) |
| quantity | 0.041 ETH (step_size 0.001 round-down from 0.0419) |
| notional | 97.6768 USDT |
| orders dispatched | 3 (entry + stop + tp) |
| accepted by Binance | yes (3/3) |
| filled | **no — entry resting 0.2% below market for full 30 min** |
| PnL | 0 USDT |

## Closeout

| Field | Value |
|---|---|
| closeout ran at | 2026-05-05T13:28:22Z (**T+28:22, inside window**) |
| cleanup grace exercised | no — closeout completed inside window, no past-end admit needed |
| flatten submitted | False (no position to flatten) |
| reconciliation | ok |
| Run ID | `01KQW56JA9A9BRX7QQS1EAF76D` |
| local flat repair | 0 |
| report file | `reports/gate6-closeout-ETHUSDT-20260505T132822Z.md` |

All 3 orders confirmed CANCELED on Binance side.

## What this canary proved

### NEW (canary 1+2 could not test)

- ✅ **Bug 4 fix (`c8985b8`) production-validated.** 57 `status='alive'`
  heartbeats written between 13:00:38Z and 13:28:38Z at exact 30s
  interval, 0 gap. Compare canary 2 where the gap between `connected`
  and the next would-be heartbeat hit ~110s and forced an operator
  user-stream restart. **Submit-canary 90s freshness gate now
  satisfied automatically through the entire window.**
- ✅ **$100 notional scale-up.** 4× canary 2's notional dispatched to
  mainnet without new exchange-side rejections, margin issues, or
  step_size rounding surprises. `qty=0.041` (step 0.001 round-down)
  produced `notional=97.6768` — comfortable above the $20
  min_notional and well below the $100 cap.
- ✅ **CLI `--entry-offset-pct 0.002`** flag works as expected; entry
  computed = mark × 0.998 = 2382.36.

### REPEATED from canary 1+2

- ✅ Authorization paperwork end-to-end (single commit, no retry)
- ✅ main.yaml mainnet flip + `git checkout` revert (clean both ways)
- ✅ user-stream `mainnet_outside_exercise_window` window-gate (start)
- ✅ submit-canary 90s heartbeat freshness check
- ✅ Broker dispatches full bracket to Binance mainnet, all 3 orders
  accepted
- ✅ Kill switch never trips during quiet idle window
- ✅ Mid-window `reconcile-live` returns `ok`
- ✅ Closeout cancel-all + sync + reconcile chain clean
- ✅ Closeout report written to `reports/gate6-closeout-*.md`

## What this canary did NOT prove (Gate A still incomplete)

**Third consecutive no-fill** across canary 1+2+3:
- canary 1: entry 0.5% — no fill
- canary 2: entry 0.5% — no fill
- canary 3: entry **0.2% (tightest yet)** — still no fill

ETH did not drop 0.2% in any of the three 30-min windows. This means
**Gate A's core target — real fill chain (entry fill → bracket
activation → SL/TP fill → position close → PnL accounting)** is
**still completely unproven** after three real-money canaries.

Specifically left for canary 4:
- entry fill → user-stream `ORDER_TRADE_UPDATE` → position row open
- SL or TP fill → row close + correct `exit_reason`
- Real PnL: gross / fees / net all correct on a real round-trip
- Bracket reduceOnly behaviour during a live close
- LiveEventGuard behaviour on a real position
- emergency_close path (only if bracket fires before window end)

## Insight: 3 consecutive no-fills suggests calm-window mismatch

The Path C plan picked **quiet UTC slots** for canary windows on
purpose — to avoid news/open volatility during system testing.
But "quiet" means narrow 30-min ranges, and ETH simply does not
swing 0.2-0.5% in calm windows often enough.

Three no-fills tells us the **canary design is pessimistically
biased against fill** — which is good for first-canary safety but
defeats the purpose of canary 3 which was specifically designed
to reach the fill chain.

**Three options for canary 4:**

1. **Tighter entry — 0.05% or 0.1%** with `--entry-offset-pct 0.0005`
   or `0.001`. Likely fills on micro-noise alone. Risk: fills on
   bid-ask flicker, not a directional move; the SL or TP fill side
   is then dominated by which side the market re-bounces toward.
2. **Longer window — 60-120 min** instead of 30 min. Statistically
   more likely to capture a 0.2%+ move. Risk: longer mainnet
   exposure, more market regime variability.
3. **Pick a window with known catalyst — e.g. funding settlement
   (00/08/16 UTC) or a US data release (CPI 12:30 UTC).**
   Almost guarantees movement. Risk: catalyst-driven volatility
   is exactly what canary windows usually avoid.

**Recommendation for canary 4:** option 2 (longer window) at
**60 min** with entry **0.1%**. Doubles the price-walk budget;
0.1% is half of canary 3's distance. Expected: high fill
probability with controlled volatility.

## Operator state at end of window

- main.yaml reverted to `live/testnet` `allow_mainnet=false`
  `micro_live.enabled=false` `max_notional=25`. Working tree clean
  for `config/main.yaml`.
- Local DB: ticket `01KQW3KET5YAD9GM9880YY2ET4` left as `accepted`
  (consistent with canary 1+2 pattern; no position row, no fill
  events, future reconciles will stay `ok`).
- Binance mainnet: 0 open orders, 0 position. Balance unchanged
  minus zero exchange-fee accruals (entry never filled, only
  cancellations — Binance does not charge for cancel-only flow).
- user-stream bg killed at 13:30:XXZ (exit 144 = SIGTERM, expected).
- Dashboard left running.

## Path C status update

| Gate | Component | Status |
|---|---|---|
| **A: System trades** | scale-up ($100) | ✅ proven |
| | Bug 4 fix | ✅ proven |
| | real fill chain | ❌ **STILL unproven** |
| | short direction | ❌ unproven |
| | concurrent positions | ❌ unproven |
| B: 24/7 hands-off | — | not started |
| C: Production-ready | — | not started |

**Gate A blocks until canary 4+ achieves a real fill.** Cannot
proceed to Gate B engineering (auto-signal + remove
exercise_window) until we have evidence the system handles a real
position end-to-end.

## Next canary (canary 4)

Per the §Insight recommendation:

- Window: **60 min** (not 30) — likely **2026-05-06 12:00-13:00 UTC**
  or operator preference
- Cap: **$100** (same as canary 3, no scale change)
- Direction: **long** (still, holding short for canary 5 — one
  variable at a time even when compressed)
- Concurrent: **1** (still)
- Entry offset: **0.1%** (`--entry-offset-pct 0.001`)
- Stop / TP: **default 1%** each
- Goal: **real fill** + correct close path. If fill happens and SL
  fires = expected loss ~$1 (1% of $100). If TP fires = +$1.
  If market doesn't move 0.1% in 60 min, we have a deeper problem
  with our market-window assumptions.
