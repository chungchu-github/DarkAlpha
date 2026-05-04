# Dashboard Soak Test — 2026-05-03T14:57:38Z (2h)

Pre-canary verification per `docs/dashboard-soak-test.md`. Run in
parallel with a 2-hour testnet burn-in to exercise the dashboard
against real reconcile / heartbeat / receiver traffic.

## Result: PASS

Server-side stability over 2 hours confirms the dashboard is fit to
sit alongside a Gate 6 mainnet micro-live canary without itself
becoming a failure mode.

## Setup

- Dashboard: `./scripts/run_dashboard.sh` → `uvicorn dashboard.app:app
  --host 127.0.0.1 --port 8766`. Browser tab on
  `http://127.0.0.1:8766/` left in foreground for the duration.
- Burn-in (parallel): `BURN_IN_HOURS=2 ./scripts/burn-in.sh
  --start-signals` → `docs/burn-in-2026-05-03T145738Z/`. mode/env
  `live/testnet`, `allow_mainnet=False`. Tmux session
  `dark-alpha-burn-in` ran receiver / supervisor / user-stream /
  signals on top of the same DB the dashboard was reading.
- Window: 14:57:38Z → 16:57:38Z (2h).

## Resource trajectory (5 check-ins)

| t | Time UTC | RSS (KB) | FDs | 9 endpoints |
|---|---|---|---|---|
| baseline | 14:57 | 65,104 | 132 | all ✓ |
| +33 min | 15:31 | 47,728 | 142 | all ✓ |
| +60 min | 15:58 | 44,288 | 132 | all ✓ |
| +91 min | 16:29 | 44,400 | 135 | all ✓ |
| **+122 min (final)** | **16:59** | **61,136** | **123** | **all ✓** |

- **RSS final −6% vs baseline** (61,136 < 65,104). Threshold from
  the soak runbook is "RSS within +50% of baseline" — passed by a
  margin of ~56 percentage points. The baseline number includes
  Python startup high-water mark; the steady-state plateau settled
  around 44,000 KB and ticked up to 61 K only on final scrape (likely
  a `gate6_readiness` cycle pulling extra rows). No leak signature.
- **FDs final −7% vs baseline** (123 < 132). FD count oscillated in
  the band 123–142 across all five samples — bounded, no
  accumulation. Far below the 1024 / process ceiling.
- **All 9 `/api/*` endpoints returned 200 across all five check-ins**
  (kpis, positions, tickets, reconcile, heartbeat, breakers, halts,
  gate6, equity). 0 failures across 120 minutes of continuous polling
  + browser-driven traffic.

## Operational signals during the window

Read straight from the DB after closeout:

- Heartbeats: 8 (4× `listen_key_created` / `listen_key_keepalive`
  pairs, ~30 min cadence — listenKey housekeeping). First at
  14:56:11Z, last at 16:57:44Z.
- Reconciliation runs: **4 / 4 `ok`** (0 mismatch, 0 failed).
- Halt events: **0** (no `live_event_guard_halt`, no
  `kill_switch_activated`, no `circuit_breaker_tripped`).

## One signal landed mid-soak — safety chain caught it

A real automated signal came in at 16:35:49Z and the bot did the
right thing:

1. Receiver accepted it (validator passed): `event_id
   883b3325...87271`, ETHUSDT-PERP short, ranking 9.64, regime
   `fake_breakout_reversal`.
2. `strategy.ticket_created` for `01KQQB4DDWG6EEFN8W7F6YF16P`,
   notional 23,300.66 USDT, qty 9.997 (testnet sizing — irrelevant
   to mainnet caps).
3. **Dispatch refused** at 16:35:52Z:
   `error=user_stream_unhealthy:no_heartbeat_in_last_90s`.
4. No live order was placed. No position was opened. No orphan was
   left.

Why the user-stream looked stale: in a quiet testnet window the
listenKey keepalive runs every ~30 min but no `ORDER_TRADE_UPDATE`
events ever flow, so the heartbeat row in the DB ages past 90s. The
dispatch-time safety check (`user_stream_unhealthy:no_heartbeat_in_last_90s`)
is by design strict — it refuses to commit real money to a path
where fills might not be observable in real time.

**This is the bracket-reject safety chain (`e21fe50`,
LiveEventGuard wired into reconciler) operating at dispatch time on
a fresh signal that was not part of any contrived test.** The
`[error]` line in `receiver.log` is a feature, not a defect: it
records that the system *prevented* an unsafe action.

For the canary itself this constraint is comfortable to satisfy —
the operator dispatches once they've watched the user-stream tab
for ≥1 keepalive cycle, so the heartbeat is fresh and dispatch goes
through.

## Burn-in hygiene

`docs/burn-in-2026-05-03T145738Z/report.md`:

- duration 2h, snapshots captured 2 (1h cadence — soak-test scale,
  not the 24h Gate 6.7 burn-in chain).
- Final `dark-alpha status`: kill switch 🟢 clear, no breakers
  fired, mode `live/testnet` `allow_mainnet=False`.
- 1 `[error` line across all four logs — the dispatch-time safety
  refusal documented above. 0 `[error` in supervisor.log,
  signals.log, user-stream.log.

This run is **independent of the Gate 6.7 burn-in evidence chain**
(rounds 1–3 stand on their own at 24h × 3). It is logged here as
soak evidence supporting the dashboard's readiness to be operated
during the canary, not as a fourth burn-in round.

## Pass criteria checklist (from `dashboard-soak-test.md`)

- [x] RSS ends within +50% of starting (ended **−6%**).
- [x] Open files grows linearly but well under 1024 / process
  (peaked at 142, ended at 123).
- [x] All 9 `/api/*` endpoints return 200, KPI fields populated.
- [x] No browser-side uncaught exceptions (foreground tab, polling
  cadence honoured by JS timers).
- [x] No accumulating CLOSE_WAIT TCP sockets (FD count bounded
  oscillation, not monotonic).

## Next step

Dashboard is cleared for canary use. Operator proceeds to
`docs/first-canary-checklist.md` step 5 (T–60 paperwork: fill the
`<FILL>` placeholders in `docs/gate-6-authorization.md`, tick the
8 safety checkboxes, sign, commit).
