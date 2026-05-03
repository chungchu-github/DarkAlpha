# Dark Alpha Live Monitor — Dashboard Runbook

A localhost-only read-only web dashboard for the Gate 6 micro-live canary
phase (and ongoing burn-ins). Single tab, dark, info-dense, refreshed
every 5–30s. **Strictly read-only in V1** — no kill-switch toggle, no
flatten button. Those are V2.

## Quick start

```bash
cd dark-alpha-auto-execution
./scripts/run_dashboard.sh
# or run detached:
nohup ./scripts/run_dashboard.sh &> logs/dashboard.log &
```

Open `http://127.0.0.1:8766/` in a browser. Within 5s every panel should
populate.

## Architecture

- **Port 8766** — separate process from the signal receiver (8765) so
  that a dashboard restart never touches the signal hot path.
- **Localhost-only** — `LocalhostOnlyMiddleware` rejects any request
  whose `client.host` is not `127.0.0.1`, `::1`, or `localhost` with a
  403. **No login**. This is sufficient for a single-operator personal
  trading bot; not suitable for shared hosts or behind proxies that
  rewrite client.host.
- **Data layer** — `src/dashboard/queries.py` is a thin set of pure read
  functions over `storage.db.get_db()` plus existing classes (`KillSwitch`,
  `CircuitBreaker`, `Gate6ReadinessReviewer`). No logic is reimplemented.
- **Routing** — `src/dashboard/routes.py` exposes one `/api/*` JSON
  endpoint per panel. Each handler is one line over `queries.X()`.
- **Page** — `src/dashboard/static/index.html` is a single static HTML
  file with vanilla `fetch()` polling. No build step, no Jinja2, no
  framework.

## Panels

| Panel | Endpoint | Cadence | What to watch for |
|---|---|---|---|
| KPI row (kill switch, mode, mainnet armed, today PnL, open positions, last reconcile) | `/api/kpis` | 5s | Kill switch must stay 🟢. Mainnet armed must stay 🟢 unless inside an authorized exercise window. |
| Live Positions | `/api/positions` | 5s | Each open live position must have non-NULL stop and TP. Age that grows without size change → ticket may be stuck. |
| Recent Tickets (10) | `/api/tickets` | 30s | Look for repeated `rejected` with the same reject_reason — pattern = systemic. |
| Reconcile History (5) | `/api/reconcile` | 30s | Anything other than `ok` must be root-caused. Single transient mismatch → look at the next snapshot. |
| User-Stream Heartbeat | `/api/heartbeat` | 5s | <90s green, 90–180s amber, >180s red. >180s → user-stream service likely dead. |
| Circuit Breakers | `/api/breakers` | 30s | Empty / all `ok` → safe. Any `tripped` row must include action and reason. |
| Recent Halts (24h) | `/api/halts` | 30s | Empty during a clean window. Any entry → safety chain fired; investigate before the next signal. |
| Gate 6 Readiness | `/api/gate6` | 30s | Should report `status: go` during a healthy run. `no_go` with a single failing check is normal mid-burn-in (e.g. heartbeat fail at start). |
| Equity sparkline | `/api/equity` | 30s | Footer. Trend line over the last 100 `equity_snapshots`. |

## What V1 deliberately does NOT do

- **No control buttons.** Kill switch toggle, breaker reset, cancel
  orders, flatten — all V2 with confirm-modal patterns. V1 is for
  observation.
- **No login / token / CSRF.** Localhost is the only auth.
- **No WebSocket push.** Polling is sufficient at 5s for hot panels.
- **No log tail / file viewer.** Run `tail -f logs/*.log` in a terminal.
- **No charts library.** Equity sparkline is hand-rolled SVG. If V2
  needs richer charts, evaluate at that point.

## Manual verification

1. `./scripts/run_dashboard.sh` in one terminal.
2. `open http://127.0.0.1:8766/` — KPI row + 8 panels populate within 5s.
3. `touch /tmp/dark-alpha-kill` → Kill Switch KPI flips red within 5s
   without page reload. `rm /tmp/dark-alpha-kill` → flips green.
4. POST a test signal to 8765 (existing receiver path) → "Recent
   Tickets" updates within 30s.
5. From another machine on the LAN: `curl http://<lan-ip>:8766/` →
   `403 Forbidden`.
6. Kill the dashboard process → POST a signal to 8765 → confirm the
   signal receiver still accepts it normally (decoupling check).
7. Leave the dashboard tab open for 30 minutes → no memory growth in
   browser dev tools, no `lsof -p <pid>` connection accumulation.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Page shows "error: ..." in every panel | Dashboard process died | Restart via the script; check `logs/dashboard.log` |
| Panels stay on last poll, indicator dot turns red | Network/DB error in one or more endpoints | `curl http://127.0.0.1:8766/api/kpis` to isolate which endpoint is failing |
| Heartbeat panel stays "no heartbeat recorded yet" | `dark-alpha user-stream listen` not running | Start it in tmux; the dashboard will pick it up on next poll |
| Gate 6 Readiness shows `no_go` (`6.5 user stream heartbeat fail`) | Same as above | Same |
| `403 Forbidden` from your own browser | You're hitting via `<lan-ip>` instead of `127.0.0.1` | Use `127.0.0.1:8766` or `localhost:8766` |
| Page loads but data is stale | Browser tab was hidden — modern browsers throttle background timers | Bring tab to front |

## V2 roadmap (deferred)

- Confirm-modal-protected control buttons (Activate Kill Switch / Resume,
  Reset Breaker, Cancel Open Orders, Flatten Position).
- CSRF token via secret cookie set on first GET /.
- WebSocket push for kill switch state change only — everything else
  remains polled.
- In-app log tail.
- Telegram cross-link.
