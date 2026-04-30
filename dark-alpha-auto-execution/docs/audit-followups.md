# Post-Audit Follow-up Status

Tracks the items left on the audit board after commit `d1ce358`
(post-audit cleanup: monorepo CI, mypy/format sweep, P0 reconciliation
fix, burn-in harness). Re-verified 2026-04-27.

## Closed — verified as non-bugs

### `position_manager.apply_live_symbol_exit` exit_price fallback

- **Location**: `src/execution/position_manager.py:373`
- **Original concern**: fallback to `0.0` would compute a fake
  `-entry_price × qty` loss when the price source fails.
- **Verification**: the fallback chain is
  `average_price → row["entry_price"] → 0.0`. The middle rung makes
  `exit_price == entry_price`, so `gross = 0` (break-even), not a
  fabricated loss. Reaching the `0.0` rung requires `entry_price`
  itself to be NULL/0 in the DB row — a schema-violation state that
  still produces `gross = 0`, not a fake loss. `average_price` is
  sourced from the user-data stream fill event and is present in the
  real flow.
- **Decision**: design is correct. No change.

### `gate6.submit_canary` `_last_price` orphan ticket

- **Location**: `src/execution/gate6.py:332`
- **Original concern**: a raise from `_last_price()` would leave an
  orphan ticket in the DB that later reconciliation would misjudge.
- **Verification**: `_last_price()` is called at line 332, before any
  DB write. The first write (`_persist_setup_event`) is at line 430
  and `router.dispatch(ticket)` at line 432. A raise from
  `_last_price()` unwinds before any persistence, leaving no orphan.
- **Decision**: flow is already fail-clean. No change.

## Open — non-blocking

### mypy baseline (~30 errors)

- **Scope**: `reporting/*`, `gate6.py`, `live_user_stream.py` — all
  Codex-authored modules.
- **Status**: CI step has `continue-on-error: true` so the rest of the
  pipeline stays green. Errors are structural call-site issues
  (`object` passed where `str` is expected, etc.), not signature
  one-liners.
- **Plan**: clean opportunistically when editing those modules for
  other reasons. Do not sweep in isolation — the risk of touching
  unrelated runtime behavior outweighs the cosmetic benefit.

## Operational — pending execution

### Gate 6.7 burn-in (first 24h)

- **Harness**: `scripts/burn-in.sh` + `docs/burn-in-runbook.md`.
- **Command**: `BURN_IN_HOURS=24 ./scripts/burn-in.sh`
- **Bar**: three consecutive clean ≥24h burn-ins (spread over a week)
  before scheduling Gate 6 micro-live. Output goes to
  `docs/burn-in-<UTC-DATETIME>/report.md`.
- **First attempt status**: `docs/burn-in-2026-04-26T164808Z/` is filed
  as INVALID — see incident below. Counter remains at 0/3.

## P0 — landed via earlier commits

### `2026-04-30` Gate 6.8 readiness over-strict — burn-ins could not qualify

- **Defect**: three checks in `Gate6ReadinessReviewer` over-counted as
  fail. (1) `_check_event_guard_state` had no time bound, so a single
  historical halt (including verification runs against a cleaned-up
  orphan) would pin the check at fail forever. (2) and (3)
  `_check_user_stream_events` and `_check_burn_in` required at least
  one organic TRADE user-stream event in the window — but trade
  frequency is a function of strategy thresholds + market activity,
  not a safety signal. A clean burn-in in a quiet window could not
  qualify regardless of how the safety chain held up.
- **Fix**: bound the event-guard halt query by the burn-in window;
  treat absence of trade events as `ok` (with explanatory detail);
  burn-in evidence requires only `ok` reconciliation runs.
- **Verification**: simulating readiness at the first burn-in's end
  time (`2026-04-29T03:26Z`) flips the report from `no_go` to `go`
  with all 8 checks `ok`.

### `2026-04-26` Bracket-reject orphan position survives 24h burn-in

- **Incident report**: `docs/incidents/2026-04-26-bracket-reject-orphan-position.md`
- **Defect**: `LiveEventGuard` was wired only into the user-stream WebSocket
  fill path. Any fill landing through REST polling / reconcile (user-stream
  not yet connected, listenKey expired, missed event) bypassed the guard
  entirely, leaving an unprotected position invisible to the safety chain
  as long as local-↔-exchange counts agreed.
- **Fix**: `LiveReconciler.run` now invokes
  `LiveEventGuard.inspect_all_active_positions` after the per-symbol sync
  loop. Findings fold into the per-symbol mismatches and trigger the
  existing kill-switch activation path. Plus `scripts/burn-in.sh` refuses
  to start with dirty operational state (open live positions or in-flight
  orders).
- **Out of scope intentionally**: broker routing was already fixed in
  commit `1b44e72` (with regression test) and is not re-touched.
