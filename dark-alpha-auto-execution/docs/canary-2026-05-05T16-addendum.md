# Canary 5 — 2026-05-05T16:00:00Z to T17:00:00Z

Fifth Gate 6 mainnet micro-live canary. **Bug 6 fix
production-validated** (exit_price + PnL backfilled correctly).
**Bug 5 fix production-validated** (0 zombie processes after
clean stop). **Bug 8 surfaced** — even with 0 zombies and a
fresh single user-stream, the WebSocket received zero
ORDER_TRADE_UPDATE events for the actual fill. Real PnL
captured locally for the first time: **net = -$0.5316 USDT**.

## Authorization

- Window: 2026-05-05T16:00:00Z → 2026-05-05T17:00:00Z (60 min)
- Symbol: ETHUSDT-PERP
- Direction: long
- Notional cap: 80 USDT
- Entry offset: 0.1% (`--entry-offset-pct 0.001`)
- Leverage: 1×
- Daily loss cap: 5 USDT
- Concurrent positions: 1
- Authorization commit: `1d9ed76`
- Bug 5 fix in HEAD: `786d28c`
- Bug 6 fix in HEAD: `50fe87a`

## Submitted ticket

| Field | Value |
|---|---|
| ticket_id | `01KQWDYBY40T9D56JEH1ZXAHRS` |
| dispatched at | 2026-05-05T16:01:16Z (T+1:16) |
| mark price | 2379.46 USDT |
| entry (LIMIT BUY) | 2377.08 (mark × 0.999) |
| stop loss | 2353.30 (entry × 0.99) |
| take profit | 2400.85 (entry × 1.01) |
| quantity | 0.033 ETH |
| notional | 78.4436 USDT |
| accepted by Binance | yes (3/3) |
| **filled** | **YES — entry filled @ 2377.08** |
| flatten exit | MARKET SELL @ 2363.34, 0.033 ETH |
| **gross PnL** | **-$0.4534 USDT** (computed by Bug 6 fix from `(exit-entry)×qty`) |
| **fees** | **$0.0782 USDT** (estimated 0.05% per side) |
| **net PnL** | **-$0.5316 USDT** (gross - fees) |

## Closeout

| Field | Value |
|---|---|
| closeout ran at | 2026-05-05T16:58:55Z (T+58:55, inside window) |
| flatten submitted | True |
| reconciliation | ok |
| Run ID | `01KQWH83N16GNNYJC9SPYPM47A` |
| local flat repair | 1 (one position closed locally + PnL backfilled) |
| report file | `reports/gate6-closeout-ETHUSDT-20260505T165855Z.md` |

## ✅ Bug 5 fix production-validated

| Check | Result |
|---|---|
| `data/user-stream.pid` written at start | ✅ contained PID 20219 |
| 0 zombie Python processes during canary | ✅ `pgrep cli.main.*user-stream` empty after start |
| `dark-alpha user-stream stop` clean shutdown | ✅ "user-stream stopped (pid 20219)" |
| PID file removed on clean exit | ✅ `data/user-stream.pid` not present after stop |

No more `pkill -f` workaround needed. Bug 5 fix end-to-end clean.

## ✅ Bug 6 fix production-validated

After canary 4, the position row had:
```
status=closed exit_reason=manual_flatten_reconciled
exit_price=""  ← MISSING
gross_pnl_usd=NULL  fees_usd=NULL  net_pnl_usd=0.0  ← WRONG
```

After canary 5, the position row has:
```
status=closed exit_reason=manual_flatten_reconciled
exit_price=2363.34
gross_pnl_usd=-0.4534
fees_usd=0.0782
net_pnl_usd=-0.5316
```

**Daily-loss tracker can now read the real -$0.5316 loss**, which is
the path-C-Gate-B-blocker that Bug 6 fix targeted. Independent verify
of `gross_pnl_usd` formula: `(2363.34 - 2377.08) × 0.033 = -0.4534`
(exact match).

## ❌ Bug 8 — user-stream receives ZERO ORDER_TRADE_UPDATE events

**This was the original Bug 5 hypothesis**: the working theory was
"zombies grab the events". Canary 5 disproves it. With:

- **0 zombies** at start (fresh PID file, no leftover Python procs)
- **1 active user-stream** the whole window (PID 20219)
- **116 alive heartbeats** over 60 min (proving WebSocket connected)
- **Single `listen_key_keepalive` heartbeat at T+30:00** (proving
  keepalive task running and Binance accepting the keepalive)
- **Zero `disconnected` events** (proving WebSocket never tripped)

→ **0 rows in `live_stream_events` from canary 5 timestamp range**.

→ **Closeout sync detected the FILL via REST polling**, not stream:
   `position.live_entry_updated filled_quantity=0.033 status=open`
   was logged DURING closeout's `live_order_sync.symbol`, not from
   user-stream's `live_user_stream.ingested`.

The fill happened on Binance, our entry order's status flipped to
FILLED on the exchange side, but our user-stream WebSocket
**never received the corresponding ORDER_TRADE_UPDATE message**.

### Bug 8 hypotheses to investigate

1. **Multi-listenKey pollution at Binance backend.** Even after we
   stopped old user-stream Python procs (canary 1-4), Binance might
   still hold the LISTEN KEYS active for their 60-min TTL. When we
   create a new listenKey for canary 5, Binance may distribute events
   to ONE of the active listenKeys non-deterministically. This was
   the canary 4 hypothesis but should NOT apply to canary 5 (60+ min
   gap from canary 4 closeout, all old listenKeys would have expired).
2. **Silent WebSocket-frame drop.** Connection alive, ping/pong
   working, but Binance fails to push ORDER_TRADE_UPDATE messages
   for our account. Could be a Binance-side glitch on this specific
   account, or a payload-size / frame-size issue.
3. **ingestor silently dropping events.** `process_event` returns
   None for any non-ORDER_TRADE_UPDATE event, but the recv loop
   doesn't log raw payloads. So if Binance sent ORDER_TRADE_UPDATE
   in a slightly different format that fails our parser, we'd see
   nothing in logs OR `live_stream_events` (since `_record_event_once`
   only writes when event_type matches).
4. **`websockets` library version-specific bug.** Maybe a particular
   handshake path on mainnet `wss://fstream.binance.com/ws` differs
   from testnet `wss://stream.binancefuture.com/ws` and our
   `websockets.connect()` call returns a stream that only delivers
   certain message types.

### Recommended Bug 8 investigation steps

1. **Add raw-frame logging.** Right after `raw = await ws.recv()`,
   log the raw payload bytes (or first 200 chars) BEFORE parsing.
   This proves whether messages are arriving at all.
2. **Add `_record_event_once` write for ALL event types**, not just
   ORDER_TRADE_UPDATE, so future canaries leave forensic trail in
   `live_stream_events` regardless of ingestor logic.
3. **Run a 5-min testnet user-stream + dispatch test** with Bug 8
   logging in place. If testnet shows events, mainnet doesn't,
   we have hypothesis #4 or #1. If neither shows events, we have
   hypothesis #2 or #3.
4. **Query Binance for active listenKeys** (no public endpoint, but
   Binance support can confirm). Or confirm by testing: create
   listenKey, wait 70 min for full TTL expiry of any old keys,
   then run a tight dispatch + see if events arrive.

## Path C Gate A status update

| Component | Status | Evidence |
|---|---|---|
| $100 scale-up dispatch | ✅ proven (canary 3) | clean dispatch at 4× canary 2 |
| Bug 4 fix (alive heartbeat) | ✅ proven (canary 3+4+5) | continuous 30s heartbeats |
| Bug 5 fix (PID file) | ✅ proven (canary 5) | 0 zombies, clean stop, PID file lifecycle |
| **Bug 6 fix (PnL backfill)** | ✅ **proven (canary 5)** | exit_price + 4 PnL fields populated correctly |
| real entry fill | ✅ proven (canary 4+5) | fill detected by reconcile |
| **PnL accounting end-to-end** | **✅ proven (canary 5)** | DB net_pnl=-0.5316, computed by Bug 6 fix |
| **Daily-loss tracker can see losses** | **✅ proven (canary 5)** | net_pnl populated, daily-loss can sum it |
| bracket SL/TP fire on fill | ❌ unproven (cancel-all reaped first both times) | |
| **user-stream ORDER_TRADE_UPDATE ingestion** | **❌ Bug 8 surfaced** | 0 stream events both fill canaries |
| short direction | ❌ unproven | |
| concurrent positions | ❌ unproven | |

**Critical**: Bug 8 means user-stream is operationally USELESS for
fill detection. The system currently relies entirely on closeout
polling to discover fills. This is acceptable for short-window
chaperoned canaries but **cannot scale to Path C Gate B (24/7
unattended)** — without stream events, the system would not know
about a fill until the next reconcile cycle, which could be 1-30
minutes late.

## Cumulative real PnL (canaries 1-5)

| Canary | Filled | Real PnL |
|---|---|---|
| 1 | no | $0 |
| 2 | no | $0 |
| 3 | no | $0 |
| 4 | yes (~14:00 UTC) | -$0.158 (gross, Binance UI) + ~$0.025 BNB fees |
| 5 | yes (~16:00 UTC) | **-$0.4534 gross, -$0.5316 net** (DB, Bug 6 fix) |
| **total** | 2 fills / 5 canaries | **≈ -$0.71 USDT** |

Well within Path C accelerated plan's expected -$50 ~ -$300 USDT
6-week range. We're on day 1.

## Operator state at end of window

- main.yaml reverted to `live/testnet` `allow_mainnet=false`
  `enabled=false` `max_notional=25` `windows=""`. Working tree
  clean for `config/main.yaml`.
- Local DB ticket `01KQWDYBY40T9D56JEH1ZXAHRS` = `accepted`,
  position `01KQWH81DN44TQMTFDS63XHNVC` = `closed` with **full
  PnL fields** (Bug 6 fix working).
- Binance mainnet: 0 open orders, 0 position. Wallet balance
  reduced by ~$0.55 USDT (canary 5 net loss ≈ -$0.53 + minor
  rounding).
- 0 user-stream processes running. PID file cleaned.
- Dashboard left running.

## Next step decision

**Path C 3-gate Gate A is now blocked on Bug 8.** Without
ORDER_TRADE_UPDATE event ingestion working, we cannot:
- Move to Gate B (24/7 unattended) — fill detection latency
  unacceptable
- Prove SL/TP firing natively (depends on stream events to
  observe the fire)
- Prove emergency_close path on a real position (same)

**Recommended next action**: do not run canary 6 yet. Investigate
Bug 8 first. Add raw-frame logging to user-stream WebSocket recv
loop, then run a short canary (15-30 min) specifically to capture
diagnostic data. Once we know whether Binance is sending events
(and we're dropping them) vs not sending at all, the fix path is
clear.
