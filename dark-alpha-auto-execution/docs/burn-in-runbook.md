# Gate 6.7 Burn-in Runbook

The burn-in produces the empirical evidence Gate 6.8 requires before any
mainnet canary is authorized: live-system uptime, reconciliation success rate,
user-stream stability, and PnL distribution under realistic load.

## Pre-conditions

Before kicking off:

1. `dark-alpha doctor` returns clean.
2. `config/main.yaml` is in `mode: shadow` **or** `mode: live` + `environment: testnet`.
   Mainnet is forbidden — `scripts/burn-in.sh` refuses to run.
3. Telegram bot configured (optional but useful for live alerts).
4. Disk has ≥ 2 GB free for logs (typical 24h run is ~200 MB).
5. Last `git status` is clean — burn-in is meant to validate the *committed*
   build, not in-progress edits.
6. Operator timezone confirmed: snapshots are timestamped UTC.

## Running

```bash
cd dark-alpha-auto-execution
BURN_IN_HOURS=24 ./scripts/burn-in.sh
# or, for an extended Gate 6.7 evidence window:
BURN_IN_HOURS=72 BURN_IN_SNAPSHOT_INTERVAL_SECONDS=3600 ./scripts/burn-in.sh
```

Optional flags:

- `--start-signals` — also launch `dark-alpha-signals` in the same tmux session
  (omit if you run it from a different terminal / repo / process supervisor).

The script writes everything to `docs/burn-in-<UTC-DATETIME>/`:

```
docs/burn-in-2026-04-27T140000Z/
├── receiver.log         tee'd uvicorn output
├── supervisor.log       tee'd dark-alpha run output
├── user-stream.log      tee'd dark-alpha user-stream output
├── signals.log          tee'd signals output (only with --start-signals)
├── snapshot-001.md      hourly status + gate-check + reconcile snapshot
├── snapshot-002.md      ...
└── report.md            final summary written when the window elapses
```

Detach without stopping: `Ctrl-B` then `D`. Reattach with
`tmux attach -t dark-alpha-burn-in`.

Stop early: `tmux send-keys -t dark-alpha-burn-in:0 C-c`. The snapshot loop
exits on the next sleep boundary; the final report still gets written.

## What "good" looks like

A burn-in is acceptable evidence for Gate 6.8 only if **all** of the following
hold across the full window:

| Signal | Acceptable | Investigate if… |
|---|---|---|
| `dark-alpha status` kill switch | clear in every snapshot | any snapshot shows ACTIVE |
| `gate-check all` (specifically gate25, gate3, gate35, gate66, gate68) | every snapshot reports `pass` | any snapshot shows `fail` |
| `reconcile-live` | `status=ok` in every snapshot | any snapshot shows `mismatch` or `failed` |
| user-stream heartbeat (gate6 readiness `6.5`) | `ok` in every snapshot ≥ 30 min into the run | any snapshot reports `no_heartbeat` |
| Open positions during shutdown | covered by stop + take_profit | any unprotected position |
| Receiver / supervisor / user-stream logs | no `ERROR` or stack trace lines | any error needs root-cause |

A single transient blip is not automatic disqualification, but **every**
deviation must be explained in `report.md` before the run counts as evidence.

## After the run

1. Review `report.md` end-to-end.
2. Commit the burn-in directory:
   ```bash
   git add docs/burn-in-<DATE>/
   git commit -m "burn-in: <DATE> <HOURS>h evidence — <summary>"
   ```
3. If you found anything that needed root-cause, file an issue or open a PR
   *before* attempting the next burn-in. Burn-ins are evidence, not retries.
4. Three consecutive clean burn-ins (≥ 24h each, spread over a week) is the
   typical bar to consider scheduling a Gate 6 micro-live exercise.

## Common failure modes

| Symptom | Likely cause | Mitigation |
|---|---|---|
| `user_stream_unhealthy` in snapshots | listenKey expired or websocket disconnect not auto-recovered | Check `user-stream.log` for repeated reconnect; verify keepalive cadence |
| `reconcile-live` returns `mismatch` | exchange has orders not in local DB or vice versa | Inspect `reconciliation_runs` table details; if only DA-prefixed orders, sync may have lagged |
| Receiver 4xx spikes | postback URL mismatch from signals service | Confirm `POSTBACK_URL` in signals `.env` |
| `daily_loss_cap` rejection in supervisor | realized loss exceeded `risk_gate.yaml` cap | Expected behavior; verify the cap is what you want |
| Disk filling up | log rotation not configured | Add a `logrotate` rule for `docs/burn-in-*/` or shorten `BURN_IN_SNAPSHOT_INTERVAL_SECONDS` |

## Not what burn-in covers

Burn-in is **system-level uptime + reconciliation evidence**. It does **not**:

- Validate strategy edge — that's the signals layer's backtest+evaluator
- Replace a real exchange canary — testnet liquidity != mainnet
- Audit the code itself — that's `code-review` / `ultrareview` / `mypy` / tests
