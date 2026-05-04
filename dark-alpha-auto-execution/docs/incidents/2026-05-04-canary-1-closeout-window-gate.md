# Incident — Closeout blocked by exercise-window gate after window end

- **Detected by**: Gate 6 first canary 1 closeout attempt at
  `2026-05-04T05:01Z`, ~1 minute after window end.
- **Severity**: P1 (would leave unattended bracket orders resting on
  mainnet indefinitely; unbounded fill risk)
- **Affected component**: `_assert_in_exercise_window` is called from
  every `assert_live_mode_enabled` invocation, including
  `gate6 closeout` and `cancel-open-orders`.
- **Status when written**: closeout was blocked at code level. Operator
  cancelled the 3 resting orders manually via Binance UI within ~1
  minute. Read-only signed GETs to `/fapi/v1/openOrders` and
  `/fapi/v2/positionRisk` confirm 0 orders / 0 position. Mainnet
  exposure now zero.

## Time line (all UTC)

| Timestamp | Event |
|---|---|
| 2026-05-04 04:30:33 | `gate6 submit-canary` succeeds. Three orders dispatched to mainnet: entry LIMIT BUY 0.01 ETH @ 2378.94, STOP_MARKET @ 2355.15, TAKE_PROFIT_MARKET @ 2402.73. Ticket `01KQRM0Y8ARV47PBZTT1FBNJXG`. |
| 2026-05-04 04:30:33 → 04:59:59 | Window open. Mark price stays above entry; no fill. user-stream connected (heartbeat amber but still connected — no events to ingest in idle window). One mid-window manual `reconcile-live` at 04:51:43 returns `count=3 status=ok`. |
| 2026-05-04 05:00:00 | Window per `config/main.yaml` `exercise_window_end` and `docs/gate-6-authorization.md` ends. Three bracket orders still resting on Binance mainnet. |
| 2026-05-04 05:01:00 (approx) | Operator runs `gate6 closeout --symbol ETHUSDT-PERP --yes` per the runbook's T+30 instruction. CLI exits non-zero with `✗ gate6 closeout blocked: mainnet_outside_exercise_window`. |
| 2026-05-04 05:02–05:03 | Operator cancels all three orders manually via Binance Futures UI. |
| 2026-05-04 05:03:19 | Read-only signed GET confirms `openOrders count=0`, `positionAmt=0`. |
| 2026-05-04 05:03:30 | `git checkout config/main.yaml` reverts to `live/testnet`, `allow_mainnet=False`, `micro_live.enabled=False`. |

## Why the gate fires when it shouldn't

`assert_live_mode_enabled(config)` is a generic preflight applied to
**every** mainnet code path. It chains:

1. `mode == live` check
2. `allow_mainnet` check
3. Authorization-file presence + freshness
4. `_assert_in_exercise_window` — fails if `now` is not within
   `[exercise_window_start, exercise_window_end]`.

`submit_gate6_canary`, `gate6 closeout`, `cancel-open-orders`,
`reconcile-live`, and `user-stream listen` all start with the same
preflight. That is correct for **opening** new exposure (you must not
submit outside the operator-authorized window) and for **continuous
monitoring** (you cannot start the WebSocket consumer for an
authorization that has expired).

It is wrong for **cleanup**: closeout's whole purpose is to act
**after** the window in order to flatten and cancel anything still
on exchange. By design `auto_cancel_flatten_after: true` is a flag
authorizing exactly this post-window cleanup. The current code path
ignores that flag at the gate level — the gate fires before any
flag-aware logic runs.

This was not caught earlier because:

- Testnet exercises previously walked through closeout **inside** the
  window (operator triggered closeout early before the window end on
  prior dry-runs). The runbook example used "if the position is still
  open at exercise window end" — the operator never actually ran the
  command across the boundary.
- Unit tests for `gate6_closeout` mock the time and run "inside" the
  window. There was no regression test for `now > window_end` calling
  `closeout`.

## Resolution (next commit, deferred — not in this commit)

Loosen `_assert_in_exercise_window` to a path-aware check, with two
acceptable behaviours:

1. **Expand the window** for cleanup paths to
   `[start, end + grace_minutes]` where `grace_minutes` defaults to
   ~5 minutes. Operator gets a small reasonable buffer to land the
   closeout command after window end. Outside the grace window
   the operator must extend authorization (which is the right path
   for an abandoned cleanup).
2. **Bypass the window check entirely for `closeout` /
   `cancel-open-orders`** when `auto_cancel_flatten_after: true` is
   set. These are reduce-only / risk-down-only operations and the
   operator authorization that opened the window also pre-authorized
   the cleanup behaviour.

Recommend approach (1) as the conservative default — small grace
window does not risk new exposure, and forces operator engagement
for any longer-lived cleanup discrepancy.

Until the fix lands, the operator workaround is documented in the
runbook update below.

## Operator runbook update (interim, until next commit)

If `gate6 closeout` blocks with `mainnet_outside_exercise_window`:

1. Manually cancel all open ETHUSDT (or chosen symbol) orders via
   Binance Futures web/app — Cancel All Open Orders is one click.
2. If a position is open, manually flatten via Market Close on the
   Binance UI.
3. Run a read-only signed GET to verify `openOrders=0` /
   `positionAmt=0` (script in this incident's verification step).
4. `git checkout config/main.yaml` to revert to testnet.
5. File the canary report manually (no `reports/gate6-closeout-*.md`
   was generated).

## Out of scope intentionally

- The `_fetch_symbol_filters` BTCUSDT-leak fix landed earlier in
  commit `a16eb66` and is not re-touched here.
- The user-stream / reconcile-live / submit-canary window-gating is
  correct for those paths and stays untouched.
