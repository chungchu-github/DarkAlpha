# Gate 6.4-6.8 Runbook

This runbook is the final micro-live hardening path. It does not authorize
mainnet by itself. `config/main.yaml` must remain `shadow/testnet` except during
an explicitly approved Gate 6 exercise window.

## Gate 6.4: User Stream Fill Verification

Goal: Binance `ORDER_TRADE_UPDATE` events must update local DB before polling.

Terminal A:

```bash
poetry run dark-alpha user-stream listen
```

Terminal B, inside an approved micro-live window only:

```bash
poetry run dark-alpha gate6 submit-canary --symbol BTCUSDT-PERP --side LONG --entry-offset-pct 0.002 --yes
```

Then verify:

```bash
poetry run dark-alpha gate6 readiness --symbol BTCUSDT-PERP --recent-stream-minutes 30 --burn-in-hours 1
poetry run dark-alpha reconcile-live --symbol BTCUSDT-PERP
```

Pass criteria:

- `live_stream_events` contains the entry/exit `TRADE` events.
- `orders.fill_quantity` and `positions.status` update without requiring
  `sync-live-orders`.
- Final reconciliation is `ok`.

## Gate 6.5: Runtime Persistence And Recovery

User stream is now a required live process alongside receiver and supervisor.

Run order:

```bash
poetry run dark-alpha user-stream listen
poetry run uvicorn signal_adapter.receiver:app --host 127.0.0.1 --port 8765
poetry run dark-alpha run
```

Crash recovery requirements:

- User stream must reconnect with backoff after WebSocket disconnect.
- listenKey keepalive must continue even when no fills arrive.
- Startup still runs reconciliation through supervisor before live ticks.
- `live_runtime_heartbeats` must show recent `user_stream` activity.

Operator check:

```bash
poetry run dark-alpha gate6 readiness --symbol BTCUSDT-PERP --require-go
```

## Gate 6.6: Event-Driven Risk Reaction

Fill ingestion now runs `LiveEventGuard` after live fill processing.

Automatic halt conditions:

- Entry fill creates an active live position but stop or take-profit protection
  is not locally active.
- Unknown non-`DACLOSE` live fill is seen in user stream.
- Existing open live position is found without a full protective bracket.

Emergency close behavior:

- `DACLOSE...` fills are treated as intentional emergency/manual flatten.
- The fill closes local live positions by symbol.
- The event is audited but does not by itself halt the system.

Deterministic check:

```bash
poetry run dark-alpha gate-check gate66
```

## Gate 6.7: Burn-In Evidence

Minimum burn-in before increasing size or relaxing manual oversight:

- BTCUSDT only.
- 1x leverage.
- `max_notional_usd <= 100`.
- At least 24 hours of user-stream heartbeat evidence.
- At least one user-stream `TRADE` event in the review window.
- At least one `ok` reconciliation in the same window.
- No `live_event_guard_halt` audit entries.

Review command:

```bash
poetry run dark-alpha gate6 readiness --symbol BTCUSDT-PERP --burn-in-hours 24 --require-go
```

## Gate 6.8: Go / No-Go Review

The final review is DB-backed:

```bash
poetry run dark-alpha gate6 readiness --symbol BTCUSDT-PERP --recent-stream-minutes 30 --burn-in-hours 24 --require-go
```

GO requires all checks to be `ok`:

- Gate 6.4 schema and recent user-stream fill event.
- Gate 6.5 recent user-stream heartbeat and latest reconciliation `ok`.
- Gate 6.6 no event-guard halt and no unprotected live position.
- Gate 6.7 burn-in evidence present.
- Gate 6.8 kill switch clear.

NO-GO actions:

```bash
poetry run dark-alpha halt --reason "gate6.8 no-go"
poetry run dark-alpha cancel-open-orders --symbol BTCUSDT-PERP --yes
poetry run dark-alpha flatten --symbol BTCUSDT-PERP --yes
poetry run dark-alpha reconcile-live --symbol BTCUSDT-PERP
```

After every exercise, restore:

```yaml
mode: shadow
live:
  environment: testnet
  allow_mainnet: false
```
