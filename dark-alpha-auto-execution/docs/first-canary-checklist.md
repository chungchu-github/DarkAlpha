# First Gate 6 Micro-Live Canary — Launch Checklist

Use this once. After the first canary closes cleanly, write a short
"second canary" addendum citing what you learned — don't reuse this
checklist verbatim, because the first canary is the one with the most
uncertainty.

## Prerequisites (must be true *before* you start filling this out)

- [x] Gate 6.7 burn-in chain 3/3 complete — `docs/audit-followups.md`
      shows three counted rounds.
- [x] Dashboard V1 deployed locally — `./scripts/run_dashboard.sh`
      runs cleanly.
- [ ] **Dashboard 1–2h soak test passed** — see
      `docs/dashboard-soak-test.md`. Record run timestamp + RSS / FD
      deltas in this file under §Notes once done.
- [ ] **Dedicated mainnet API key created** in Binance:
      - Permission: **Read + Futures Trading only**. Withdrawal **OFF**.
      - IP whitelist: your home/office IP (and only that).
      - Label: e.g. "DarkAlpha-canary-2026" so it's distinguishable.
- [ ] `BINANCE_FUTURES_MAINNET_API_KEY` and
      `BINANCE_FUTURES_MAINNET_API_SECRET` written to monorepo-root
      `.env` only (`/Users/darkagent001/DarkAlpha/.env`).
      **Do not** create a package-level .env in
      `dark-alpha-auto-execution/` or `dark-alpha-signals/` — both
      services already read the workspace .env (commit `c908219`).
- [ ] Mainnet account funded with **at least 2× the daily-loss cap +
      1× max-notional**. With the recommended caps below
      (notional $10, daily loss $5, leverage 1x) that's ~$20 USDT
      sitting in the Futures wallet — pure ETH or USDT, your call.

## Recommended starting parameters

These mirror the codebase's stated design intent
(`docs/gate-6-micro-live-runbook.md` example) and are suitable for
the **first** canary specifically. Subsequent canaries can scale up
once you have empirical confidence.

| Parameter | Recommended | Why |
|---|---|---|
| Symbol | `ETHUSDT-PERP` | Lower min-notional than BTC (~$5 vs ~$100), so the absolute exposure is genuinely micro. Liquid enough for clean fills. |
| Direction | `long` only | One direction = one less variable. Switch to `both` after 2+ successful canaries. |
| Max notional / order USDT | `10` | Above Binance min-notional, well below any meaningful financial impact. |
| Max leverage | `1` | No leverage — full exposure to mark moves only, no liquidation cascade risk. |
| Max daily loss USDT | `5` | Half the per-order notional. ~5 stop-outs at 1% before the breaker fires. |
| Max concurrent positions | `1` | Don't multiply exposure during a debug exercise. |
| Exercise window | 30 minutes | Long enough to fully exercise dispatch → fill → close-out flow. Short enough to bound exposure if anything misbehaves. |
| Exercise window timing | a quiet 30-min slot when **you can sit and watch the dashboard** | Not overnight, not during news, not during funding settlement. Sunday 02–06 UTC is generally quiet. |

These are operational risk caps, not investment guidance — operator
decides their own risk tolerance. The values above reflect the
codebase's existing design intent; deviating upward should be a
conscious choice with documented rationale in §Notes.

## On canary day

Work top-to-bottom; don't skip ahead.

### T–60 min: rehearse paperwork

```bash
cd /Users/darkagent001/DarkAlpha/dark-alpha-auto-execution
git status   # must be clean (canary validates the *committed* build)
git pull --ff-only   # confirm you're on origin/main HEAD
```

Open `docs/gate-6-authorization.md`. Update:

1. `Generated at UTC` → now (UTC).
2. `Authorized symbol`, caps — match the table above.
3. `Exercise window start UTC` and `end UTC` — your chosen 30-min slot.
4. Tick all 8 safety acknowledgement boxes (`[x]`). If any cannot
   honestly be ticked, **stop**.
5. Update `Matching config/main.yaml Block` to mirror your edits.
6. Sign with date and your operator handle.

Commit + push the authorization update so the canary runs against a
committed config:

```bash
git add docs/gate-6-authorization.md
git commit -m "auth: gate 6 canary 1 — ETH/USDT 10 notional / 5 USDT daily cap / 30min window"
git push
```

### T–45 min: edit `config/main.yaml`

```yaml
mode: live
live:
  environment: mainnet
  allow_mainnet: true
  require_gate_authorization: true
  gate_authorization_file: docs/gate-6-authorization.md
  micro_live:
    enabled: true
    allowed_symbols:
      - ETHUSDT-PERP
    max_notional_usd: 10
    max_leverage: 1
    max_daily_loss_usd: 5
    max_concurrent_positions: 1
    require_stop_loss: true
    require_take_profit: true
    exercise_window_start: "<your start ISO8601 UTC>"
    exercise_window_end:   "<your end ISO8601 UTC>"
    auto_cancel_flatten_after: true
```

**Do not commit this main.yaml flip** — it's an exercise-window-only
state. Revert immediately after closeout. The committed default stays
`live/testnet`.

### T–30 min: pre-flight

```bash
poetry run dark-alpha doctor             # all-green
poetry run dark-alpha gate-check all     # every gate "ok"
poetry run dark-alpha reconcile-live     # status=ok, kill switch 🟢
```

If `gate-check all` reports anything other than all-ok, **stop and
investigate**. Do not proceed with mainnet money over a yellow gate.

### T–10 min: start observation tools

In separate terminals:

```bash
# Terminal 1: dashboard
./scripts/run_dashboard.sh
# (open http://127.0.0.1:8766/ in browser, foreground tab)

# Terminal 2: receiver
poetry run uvicorn signal_adapter.receiver:app --host 127.0.0.1 --port 8765

# Terminal 3: user-stream (mainnet)
poetry run dark-alpha user-stream listen

# Terminal 4: signals (if running automated; skip for first canary if manual)
cd ../dark-alpha-signals && poetry run python -m dark_alpha_phase_one.main
```

For **first canary specifically**, recommend: skip the signals service
and do **one manual canary submission** instead — see next step.

### T+0: dispatch one canary, watch it fill, watch it close

In yet another terminal:

```bash
# Submit one canary ticket — real money, real fill
poetry run dark-alpha gate6 submit-canary \
  --symbol ETHUSDT-PERP \
  --side LONG \
  --yes
```

Then **do nothing**. Watch the dashboard:

- KPI: kill switch stays 🟢, mainnet armed flips 🔴 (expected during
  exercise window), open positions = 1 once filled.
- Live Positions panel: row appears with stop and TP populated.
- Recent Tickets: ticket transitions `accepted` → `filled`.
- Reconcile: `status=ok` after each cycle.
- Recent Halts: must stay empty.

If the position closes itself (stop or TP hit), let it. The bracket
is the safety mechanism.

### T+30 min: closeout

If the position is still open at exercise window end:

```bash
poetry run dark-alpha gate6 closeout --symbol ETHUSDT-PERP --yes
```

This runs `cancel-open-orders` + `flatten` + `sync-live-orders` +
`reconcile-live` and writes `reports/gate6-closeout-...md`.

### T+35 min: paperwork

```bash
# Revert main.yaml back to testnet defaults
git checkout dark-alpha-auto-execution/config/main.yaml
poetry run dark-alpha reconcile-live   # must be clean
```

Verify the closeout report. Commit any post-canary evidence (e.g. a
signed addendum noting "canary 1 clean — net PnL = $X"). **Do not
commit the mainnet-armed main.yaml.**

## Notes (fill in after canary)

- Soak test run timestamp:
- Soak test RSS delta:
- Soak test FD delta:
- Canary 1 timestamp (UTC):
- Canary 1 fill price:
- Canary 1 close reason (TP / stop / manual):
- Canary 1 net PnL:
- Anything unexpected:
- Next canary planned for:

## Hard stop conditions during the canary

Halt immediately and write a closeout report **even if the window
isn't done** if any of these happen:

- Kill switch fires for any reason.
- Reconcile reports `mismatch` or `failed`.
- A position appears in Live Positions panel without stop or TP.
- Dashboard panel returns 500 / 503 (the dashboard itself misbehaving
  is not in scope of "canary failed", but losing observability during
  a canary is — switch to CLI tools and close out).
- Anything makes you uncertain. The canary's value is what it teaches;
  there's no value in pushing through an ambiguous failure.

## After the first clean canary

- File the closeout report into `docs/canary-2026-XX-XX/`.
- Do not run a second canary on the same day. Wait at least 24h.
- Plan canary 2 with the same caps OR (operator's call) one
  parameter scaled up — never multiple parameters at once.
