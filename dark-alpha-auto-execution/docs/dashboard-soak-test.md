# Dashboard Soak Test — Pre-Canary Verification

Before any Gate 6 micro-live canary touches mainnet money, the
dashboard must be proven stable under sustained operation. A 1–2 hour
soak test catches the kind of issue (slow file-descriptor leak,
unbounded memory growth, periodic exception that recovers) that the
unit tests + 5-minute smoke test cannot.

## Pre-flight (5 min)

```bash
cd /Users/darkagent001/DarkAlpha/dark-alpha-auto-execution
git status                    # must be clean
poetry run pytest -q tests/unit/test_dashboard_queries.py tests/unit/test_dashboard_routes.py
poetry run dark-alpha doctor  # must be all-green
lsof -i :8766                 # must return nothing (no stale dashboard)
```

If anything fails, fix before continuing.

## Run the soak (1–2 hours)

In one terminal:

```bash
cd /Users/darkagent001/DarkAlpha/dark-alpha-auto-execution
./scripts/run_dashboard.sh
```

In a browser: open `http://127.0.0.1:8766/` and **leave the tab in the
foreground**. Browsers throttle background timers — the soak only
exercises the polling loop if the tab is visible.

Optional but recommended: in another terminal, start the supervisor
and user-stream in the background so the dashboard is observing real
state, not just stale rows:

```bash
# tmux session called "soak" so it's distinct from any burn-in session
tmux new-session -d -s soak -n receiver \
  "poetry run uvicorn signal_adapter.receiver:app --host 127.0.0.1 --port 8765"
tmux new-window -t soak -n supervisor "poetry run dark-alpha run"
tmux new-window -t soak -n user-stream "poetry run dark-alpha user-stream listen"
```

This exercises the heartbeat/reconcile/audit panels with live data.

## What to watch (every 15–30 min, takes 1 min)

```bash
PID=$(lsof -ti :8766)
echo "RSS (KB): $(ps -o rss= -p $PID | tr -d ' ')"
echo "Open files: $(lsof -p $PID | wc -l | tr -d ' ')"
curl -sf http://127.0.0.1:8766/api/kpis | python3 -m json.tool | head -20
```

Acceptable:

- RSS ends within +50% of starting value (some growth is normal due
  to Python list/dict allocation patterns).
- Open files grows linearly but stays well under 1024 per process
  for a 2h run.
- All 9 `/api/*` endpoints return 200; KPI fields populated.
- Browser DevTools → Network shows poll requests succeeding;
  Performance Monitor shows JS heap not climbing unbounded.

Unacceptable (must investigate before canary):

- RSS more than doubles.
- Open files crosses 1000 within 2h.
- Any endpoint starts returning 500 / 503.
- Browser console shows uncaught JS exceptions.
- `lsof -p $PID` shows accumulating CLOSE_WAIT TCP sockets (network
  cleanup bug).

## After the soak

```bash
# stop the dashboard
lsof -ti :8766 | xargs kill
# stop the bot if you started it
tmux kill-session -t soak
```

If the soak passed, **proceed to `docs/first-canary-checklist.md`**.

If it failed, file an issue with: starting RSS, ending RSS, FD count
trend, the failing curl output. Fix the leak / fault before
attempting another soak. **Burn-in evidence chain stays at 3/3 — soak
test is independent of those.**
