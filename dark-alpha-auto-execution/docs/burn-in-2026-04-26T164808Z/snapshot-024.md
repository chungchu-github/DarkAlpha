# Burn-in snapshot 24 — 2026-04-27T15:51:56Z

## dark-alpha status

```

=== Dark Alpha Auto-Execution Status ===

Kill switch : 🟢 clear
  Sentinel  : /tmp/dark-alpha-kill

Circuit breakers:
  (no breakers have fired)

Live execution:
  mode               : live
  environment        : testnet
  allow_mainnet      : False
  micro_live enabled : False
  exercise_window    : — → —
  mainnet live armed : 🟢 no  (mode/environment is safe)

```

## gate-check all

2026-04-27 23:51:58 [info     ] position.live_entry_updated    filled_quantity=0.005 status=partial ticket_id=GATE25SL
2026-04-27 23:51:58 [info     ] live_order_sync.symbol         count=3 symbol=BTCUSDT-PERP
2026-04-27 23:51:58 [info     ] position.live_entry_updated    filled_quantity=0.01 status=open ticket_id=GATE25SL
2026-04-27 23:51:58 [info     ] live_order_sync.symbol         count=3 symbol=BTCUSDT-PERP
2026-04-27 23:51:58 [info     ] position.live_exit_updated     closed_quantity=0.01 reason=stop_loss ticket_id=GATE25SL
2026-04-27 23:51:58 [info     ] live_order_sync.symbol         count=2 symbol=BTCUSDT-PERP
2026-04-27 23:51:58 [info     ] position.live_entry_updated    filled_quantity=0.01 status=open ticket_id=GATE25TP
2026-04-27 23:51:58 [info     ] live_order_sync.symbol         count=4 symbol=BTCUSDT-PERP
2026-04-27 23:51:58 [info     ] position.live_exit_updated     closed_quantity=0.01 reason=take_profit ticket_id=GATE25TP
2026-04-27 23:51:58 [info     ] live_order_sync.symbol         count=3 symbol=BTCUSDT-PERP
2026-04-27 23:51:58 [info     ] live_order_sync.symbol         count=2 symbol=BTCUSDT-PERP
2026-04-27 23:51:58 [info     ] live_reconciliation.recorded   run_id=01KQ7T7SXX2VDC4E9ZCDS9D2N0 status=started
2026-04-27 23:51:58 [info     ] live_reconciliation.recorded   run_id=01KQ7T7SXX2VDC4E9ZCDS9D2N0 status=ok
2026-04-27 23:51:58 [info     ] live_reconciliation.recorded   run_id=01KQ7T7SYAQT6TR963KGFJ2RHB status=started
2026-04-27 23:51:58 [info     ] live_order_sync.symbol         count=3 symbol=BTCUSDT-PERP
2026-04-27 23:51:58 [info     ] live_reconciliation.recorded   run_id=01KQ7T7SYAQT6TR963KGFJ2RHB status=mismatch
2026-04-27 23:51:58 [critical ] kill_switch.ACTIVATED          reason='live_reconciliation_mismatch:BTCUSDT-PERP:unexpected_exchange_orders=DAUNEXPECTED' sentinel=/var/folders/dd/n5lxjb695mxdbx5drwg2yksm0000gn/T/tmpmg6ihh8w/gate-check.kill
2026-04-27 23:52:00 [warning  ] risk_gate.reject               detail= event_id=gate3-kill reason=kill_switch_active
2026-04-27 23:52:00 [critical ] kill_switch.ACTIVATED          reason=gate35 sentinel=/var/folders/dd/n5lxjb695mxdbx5drwg2yksm0000gn/T/tmpnktxkva5/gate-check.kill
2026-04-27 23:52:01 [warning  ] risk_gate.reject               detail= event_id=gate35-base reason=kill_switch_active
2026-04-27 23:52:01 [warning  ] kill_switch.cleared            sentinel=/var/folders/dd/n5lxjb695mxdbx5drwg2yksm0000gn/T/tmpnktxkva5/gate-check.kill
2026-04-27 23:52:01 [warning  ] risk_gate.reject               detail='1.00 < 1000.00' event_id=gate35-base reason=below_min_equity
2026-04-27 23:52:01 [warning  ] risk_gate.reject               detail=BTCUSDT-PERP event_id=gate35-base reason=duplicate_symbol
2026-04-27 23:52:01 [info     ] position.live_entry_updated    filled_quantity=0.01 status=open ticket_id=GATE64STREAM
2026-04-27 23:52:01 [info     ] live_user_stream.event_processed action=known_order:entry client_order_id=DAENBGATE64STREAM event_id=1777305121608:DAENBGATE64STREAM:1:TRADE:FILLED:0.01 status=FILLED symbol=BTCUSDT-PERP
2026-04-27 23:52:01 [info     ] position.live_exit_updated     closed_quantity=0.01 reason=stop_loss ticket_id=GATE64STREAM
2026-04-27 23:52:01 [info     ] live_user_stream.event_processed action=known_order:stop client_order_id=DASTSGATE64STREAM event_id=1777305121615:DASTSGATE64STREAM:2:TRADE:FILLED:0.01 status=FILLED symbol=BTCUSDT-PERP
2026-04-27 23:52:01 [info     ] position.live_entry_updated    filled_quantity=0.01 status=open ticket_id=GATE66GUARD
2026-04-27 23:52:01 [debug    ] audit.logged                   decision=activate event_type=live_event_guard_halt reason=live_position_missing_protective_orders:BTCUSDT-PERP:GATE66GUARD:stop source=live_event_guard
2026-04-27 23:52:01 [critical ] kill_switch.ACTIVATED          reason=live_position_missing_protective_orders:BTCUSDT-PERP:GATE66GUARD:stop sentinel=/var/folders/dd/n5lxjb695mxdbx5drwg2yksm0000gn/T/tmppqsrdm1b/gate-check.kill
2026-04-27 23:52:02 [error    ] live_event_guard.halted        reason=live_position_missing_protective_orders:BTCUSDT-PERP:GATE66GUARD:stop
2026-04-27 23:52:02 [info     ] live_user_stream.event_processed action=known_order:entry client_order_id=DAENBGATE66GUARD event_id=1777305121637:DAENBGATE66GUARD:1:TRADE:FILLED:0.01 status=FILLED symbol=BTCUSDT-PERP
2026-04-27 23:52:02 [warning  ] kill_switch.cleared            sentinel=/var/folders/dd/n5lxjb695mxdbx5drwg2yksm0000gn/T/tmppqsrdm1b/gate-check.kill
2026-04-27 23:52:03 [debug    ] audit.logged                   decision=record event_type=live_untracked_order_fill reason=live_untracked_order_fill:BTCUSDT-PERP:MANUALORDER1 source=live_event_guard
2026-04-27 23:52:03 [debug    ] audit.logged                   decision=activate event_type=live_event_guard_halt reason=live_untracked_order_fill:BTCUSDT-PERP:MANUALORDER1 source=live_event_guard
2026-04-27 23:52:03 [critical ] kill_switch.ACTIVATED          reason=live_untracked_order_fill:BTCUSDT-PERP:MANUALORDER1 sentinel=/var/folders/dd/n5lxjb695mxdbx5drwg2yksm0000gn/T/tmppqsrdm1b/gate-check.kill
2026-04-27 23:52:04 [error    ] live_event_guard.halted        reason=live_untracked_order_fill:BTCUSDT-PERP:MANUALORDER1
2026-04-27 23:52:04 [info     ] position.live_entry_updated    filled_quantity=0.01 status=open ticket_id=GATE68READY
2026-04-27 23:52:04 [info     ] live_user_stream.event_processed action=known_order:entry client_order_id=DAENBGATE68READY event_id=1777305124461:DAENBGATE68READY:1:TRADE:FILLED:0.01 status=FILLED symbol=BTCUSDT-PERP
# Gate 2.5 Check

- status: `ok`

- `ok` entry partial fill updates local position
- `ok` entry full fill opens local live position
- `ok` stop fill closes local live position
- `ok` take-profit fill closes local live position
- `ok` cancel/sync/reconcile leaves no orphan orders
- `ok` emergency flatten submits reduce-only market close

# Gate 3 Check

- status: `ok`

- `ok` restart sees existing submitted clientOrderId
- `ok` duplicate live ticket would be blocked before broker
- `ok` reconcile mismatch activates kill switch
- `ok` kill switch blocks new trading decisions

# Gate 3.5 Check

- status: `ok`

- `ok` kill switch rejects signal
- `ok` minimum equity rejects signal
- `ok` duplicate symbol rejects signal
- `ok` missing protective bracket is rejected before exchange
- `ok` mainnet symbol allowlist rejects unsafe symbol
- `ok` mainnet notional cap rejects oversized ticket

# Gate 5 Check

- status: `ok`

- `ok` mainnet preflight refuses incomplete runtime
- `ok` mainnet preflight requires explicit caps/window

# Gate 6 Check

- status: `ok`

- `ok` micro-live canary ticket passes explicit caps
- `ok` generic broker is available for gated mainnet execution

# Gate 6.4 Check

- status: `ok`

- `ok` ORDER_TRADE_UPDATE entry fill opens local position
- `ok` ORDER_TRADE_UPDATE stop fill closes local position
- `ok` stream event de-duplication is active

# Gate 6.6 Check

- status: `ok`

- `ok` entry fill without full protective bracket activates kill switch
- `ok` untracked manual fill activates kill switch

# Gate 6.8 Check

- status: `ok`

- `ok` readiness reviewer requires stream, heartbeat, reconciliation, guard, burn-in
- `ok` complete local evidence produces GO

## reconcile-live

```
2026-04-27 23:52:05 [info     ] live_reconciliation.recorded   run_id=01KQ7T80B7RMB5AV3S16PCM73H status=started
2026-04-27 23:52:07 [info     ] live_reconciliation.recorded   run_id=01KQ7T80B7RMB5AV3S16PCM73H status=ok
run_id=01KQ7T80B7RMB5AV3S16PCM73H status=ok
  BTCUSDT-PERP: ok
```
