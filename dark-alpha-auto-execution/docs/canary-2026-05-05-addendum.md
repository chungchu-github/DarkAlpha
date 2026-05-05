# Canary 2 — 2026-05-05T12:00:00Z to T12:30:00Z

Second Gate 6 mainnet micro-live canary. **Result: zero PnL, both
canary-1 bug fixes validated end-to-end on Binance mainnet,
closeout report written cleanly inside the 10-min cleanup grace.**

This is Path C accelerated plan **week 1 day 1**.

## Authorization

- Window: 2026-05-05T12:00:00Z → 2026-05-05T12:30:00Z (30 min)
- Symbol: ETHUSDT-PERP
- Direction: long only
- Notional cap: 25 USDT (same as canary 1 retry)
- Leverage: 1×
- Daily loss cap: 5 USDT
- Concurrent positions: 1
- Authorization commit: `1fe56b0` (single-commit, no retry needed)
- Pre-canary verification: 414/414 unit tests pass at 11:45 UTC.

## Submitted ticket

| Field | Value |
|---|---|
| ticket_id | `01KQW0A59FGHDFJJDZ6Z4H1PAV` |
| dispatch_ref | `live:01KQW0A59FGHDFJJDZ6Z4H1PAV` |
| dispatched at | 2026-05-05T12:03:01Z (T+0:03:01) |
| mark price | 2376.68 USDT |
| entry (LIMIT BUY) | 2364.79 (mark × 0.995) |
| stop loss | 2341.14 (entry × 0.99) |
| take profit | 2388.44 (entry × 1.01) |
| quantity | 0.01 ETH |
| notional | 23.6479 USDT |
| orders dispatched | 3 (entry + stop + tp) |
| accepted by Binance | yes (3/3) |
| filled | no — entry resting below market for full 30 min |
| PnL | 0 USDT |

## Closeout

| Field | Value |
|---|---|
| closeout ran at | 2026-05-05T12:30:57Z (**T+0:57s past window end**) |
| cleanup grace used | yes — admitted by `cleanup_grace_seconds=600` |
| flatten submitted | False (no position to flatten) |
| reconciliation | ok |
| Run ID | `01KQW1XFT5J7K7GY12GYGHQK3Y` |
| local flat repair | 0 |
| report file | `reports/gate6-closeout-ETHUSDT-20260505T123100Z.md` |

All 3 orders confirmed CANCELED on Binance side (`DAENB9...`,
`DASTS9...`, `DATPS9...`); local DB matches; 0 filled.

## What this canary proved (vs canary 1)

### NEW (canary 1 could not test)

- ✅ **Bug 3 fix (`07a4800` cleanup grace) validated in production**.
  `gate6 closeout` ran 57 seconds past `exercise_window_end` and was
  admitted by the 10-min `cleanup_grace_seconds` opt-in. Canary 1's
  same scenario was rejected with `mainnet_outside_exercise_window`
  and required manual Binance UI cancellation.
- ✅ **Bug 1 fix (`a16eb66` exchange-filter)** path executed
  cleanly — `min_notional` check passed for ETHUSDT $25, no BTCUSDT
  leak. (Canary 1 first attempt aborted here; retry committed the
  fix.)
- ✅ **Closeout report written** to `reports/gate6-closeout-*.md`
  for the first time on a real run. Canary 1 never reached the
  report-writing path.
- ✅ Reconciliation status `ok` returned by closeout pipeline,
  validating the post-window cleanup→reconcile chain.

### REPEATED from canary 1

- ✅ Authorization paperwork end-to-end (sign / commit / push)
- ✅ main.yaml mainnet flip + revert (this revert via
  `git checkout config/main.yaml` was clean)
- ✅ user-stream `mainnet_outside_exercise_window` window-gate (start)
- ✅ submit-canary `mainnet_outside_exercise_window` window-gate (start)
- ✅ submit-canary 90s heartbeat freshness check (see §Operational
  observation below)
- ✅ Broker dispatches full bracket to Binance mainnet, all 3
  orders accepted
- ✅ Kill switch never trips during quiet idle window
- ✅ Mid-window read-only signed GETs work

## What this canary did NOT prove (left to canary 3+)

- entry-fill → bracket-activation → stop/TP fill chain on real fills
  (Path C week 1 day 2-3 will likely encounter this with $100 cap)
- emergency_close path on a real position
- multi-symbol behaviour
- short direction path
- concurrent positions

## Operational observation — 90s heartbeat vs 30-min listenKey keepalive

**Found:** the `submit-canary` 90-second heartbeat freshness check
and the `user-stream` listenKey keepalive interval (30 min) are in
tension for 30-minute exercise windows.

- `user_stream` only writes heartbeats on `connected`,
  `listen_key_created`, `listen_key_keepalive` (every 30 min), and
  on actual stream events (fills, cancels).
- In a quiet ETHUSDT window with no fills, heartbeats stop after
  the initial `connected`. After ~90s the dispatch gate refuses.
- During canary 2, the first `connected` was at 12:00:10Z and the
  first dispatch attempt at 12:01:50Z was rejected with
  `user_stream_unhealthy:no_heartbeat_in_last_90s`.
- **Workaround used:** killed and restarted user-stream at 12:02:38Z;
  fresh `connected` at 12:02:40Z; dispatched at 12:03:01Z (within
  21s of fresh heartbeat). Worked.

This is a usability bug, not a safety bug — fail-closed behaviour
is correct, but it forces the operator into a tight choreography.
**Recommended fix:** add a periodic 30s no-op heartbeat from the
user-stream WebSocket loop while the connection is healthy. Tracked
as Bug 4 in audit-followups.

## Operator state at end of window

- main.yaml reverted to `live/testnet` `allow_mainnet=false`
  `micro_live.enabled=false`. Working tree clean for
  `config/main.yaml`.
- Local DB: ticket `01KQW0A59FGHDFJJDZ6Z4H1PAV` left as `accepted`
  (consistent with canary 1's historical record style — there is no
  matching position row because entry never filled, and Binance has
  0 open orders + 0 position so future reconciles will stay `ok`).
- Binance mainnet: 0 open orders, 0 position, balance unchanged
  minus exchange-fee accruals (none — entry never filled, only
  cancellations).
- user-stream bg process killed at 12:31:XXZ. Dashboard left running
  for continued observability.

## Path C week 1 day 1 retrospective

**Compression worked.** Canary 1 → canary 2 gap was ~32h (canary 1
ended ~05:00 UTC 2026-05-04, canary 2 dispatched 12:03 UTC
2026-05-05). Original plan suggested ≥24h between canaries; we used
that gap to ship two bug fixes (`07a4800`, `4d66e41`) and update
operator defaults — both bug fixes proved correct on this canary.

**Operational lessons for canary 3:**

1. The user-stream restart workaround is mechanical; bake it into
   the launch checklist or fix Bug 4 first.
2. The 30-min window with a 0.5%-below-market entry has a low
   probability of real fill in calm UTC slots. Canary 3 should
   consider:
   - Longer window (1-2h) to give the entry a chance to fill, OR
   - Tighter entry distance (0.2% below mark) to fill more
     reliably, OR
   - Accept that some canaries will be "no-fill" and value them
     for the dispatch+closeout chain validation alone.
3. Bug 3 fix being validated in real conditions is significant —
   the codepath that previously required operator manual fallback
   (Binance UI cancel) now self-heals. Closeout reliability has
   gone from "race against the strict gate" to "10-min comfortable
   buffer."

## Next canary

Per Path C plan, **canary 3 = ETH $100 / 1× / long / 1 concurrent /
30-min window**. Same direction + concurrency, scale notional 4×.

Decision pending: rerun same $25 cap (more reproducibility evidence)
vs proceed to $100 (Path C compression as designed). Recommend
**proceed to $100** — canary 1 + canary 2 already gives two clean
$25 dispatches; another would not add new information.
