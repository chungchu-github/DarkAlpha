# Addendum — burn-in 2026-04-30T042100Z

**Round 2/3 of the Gate 6.8 burn-in evidence chain.**

## Status

- **`status: go`** — first burn-in to pass with the post-`37fae07`
  readiness reviewer in production. All 8 checks `ok` in the frozen
  `report.md`.
- 24/24 hourly snapshots show kill switch 🟢 clear and
  `reconcile-live status=ok`.
- No `LiveEventGuard` halts inside the window.
- No live position left unprotected at finish.
- DB confirmed CLEAN at end (no in-flight orders, no open live
  positions).

## Notable event — broker margin rejection (safety chain functioning)

At `2026-05-01 04:21:38` (right at the end of the 24h window) the
strategy chain attempted to dispatch ticket
`01KQG0VJNZXWMVXTSETP5J88QD` for BTCUSDT-PERP. Binance responded with
HTTP 400, code `-2019` "Margin is insufficient." The receiver.log
captured two `[error` lines for this single event:

```
live_broker.submit_failed
  cancel_status=cancel_confirmed
  error='...code -2019, Margin is insufficient'
  failed_role=entry partial_acks=0
strategy.dispatch_failed
  error='...code -2019, Margin is insufficient'
```

This is the safety chain working as intended:

| Step | Behaviour | Evidence |
|---|---|---|
| Broker sees -2019 | Fail-closed cancel sweep | `cancel_status=cancel_confirmed` |
| No partial fills to flatten | No-op, defensive | `partial_acks=0` |
| Ticket terminal state | `status=rejected` with full reject_reason | Verified post-burn-in via DB query |
| Kill switch | Did not spuriously fire | snapshot-024 still 🟢 |
| Position table | No orphan created | DB CLEAN at end |

Encountering a real broker rejection mid-burn-in and handling it
without leaks or kill-switch noise is *better* evidence than 24h of
no-events.

## Operational signals

| Signal | Window | Result |
|---|---|---|
| Kill switch | 24/24 snapshots | 🟢 clear |
| `reconcile-live status` | 24/24 snapshots | `ok` (23 in-window per readiness count) |
| `gate6.6 open positions protected` | 24/24 snapshots | `ok` |
| `gate6.6 event-driven guard` | end-of-window readiness | `ok` (no halts in window) |
| `LiveEventGuard` halts inside window | — | none |
| supervisor / user-stream / signals `[error` lines | full window | 0 |
| receiver `[error` lines | full window | 2, both for the single -2019 event above |

## Frozen `report.md` Go/No-Go (recorded as-generated)

```
status: go
6.4 schema installed                 ok
6.4 recent fill events ingested      ok   (no organic trades, stream uptime via 6.5)
6.5 user stream heartbeat            ok   listen_key_keepalive
6.5 latest reconciliation            ok   2026-05-01 03:24:36
6.6 event-driven guard               ok
6.6 open positions protected         ok
6.7 burn-in evidence                 ok   reconciliations=23, organic_trades=0
6.8 kill switch clear                ok
```

## Round counter

- Round 1/3 — counted (see `burn-in-2026-04-28T032621Z-addendum.md`).
- Round 2/3 — **counted** (this addendum).
- Round 3/3 — pending. Recommend starting at a different session
  window than 03–04 UTC; rounds 1+2 are both around the Asia/Europe
  hand-off, so round 3 should land in US session for breadth.
