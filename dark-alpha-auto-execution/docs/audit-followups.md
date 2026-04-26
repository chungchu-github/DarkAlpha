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
