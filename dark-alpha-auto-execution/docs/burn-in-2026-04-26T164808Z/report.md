# Burn-in Report — 2026-04-26T164808Z

- duration:           24h
- mode/environment:   live/testnet
- snapshots captured: 24
- snapshot interval:  3600s

## Final dark-alpha status

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

## Final Gate 6 readiness

# Gate 6.8 Go/No-Go Review

- report_id: `01KQ7XNZY31BB8KTCGH19XVAWP`
- status: `no_go`

| Gate | Check | Status | Detail |
|---|---|---|---|
| 6.4 | schema installed | `ok` |  |
| 6.4 | recent fill events ingested | `fail` | no TRADE user-stream events in last 30m |
| 6.5 | user stream heartbeat | `ok` | listen_key_keepalive |
| 6.5 | latest reconciliation | `ok` | 2026-04-27 15:52:07 |
| 6.6 | event-driven guard | `ok` |  |
| 6.6 | open positions protected | `fail` | BTCUSDT |
| 6.7 | burn-in evidence | `fail` | requires 24h window with stream events and ok reconciliation |
| 6.8 | kill switch clear | `ok` |  |

## Performance

# Shadow Performance Report

## By Symbol

| Key | Trades | Win Rate | Gross | Fees | Net |
|-----|-------:|---------:|------:|-----:|----:|
| n/a | 0 | 0.0% | +0.00 | 0.00 | +0.00 |

## By Strategy

| Key | Trades | Win Rate | Gross | Fees | Net |
|-----|-------:|---------:|------:|-----:|----:|
| n/a | 0 | 0.0% | +0.00 | 0.00 | +0.00 |

## By Regime

| Key | Trades | Win Rate | Gross | Fees | Net |
|-----|-------:|---------:|------:|-----:|----:|
| n/a | 0 | 0.0% | +0.00 | 0.00 | +0.00 |


## Daily snapshot (today)

2026-04-28 00:52:10 [info     ] reporting.daily_snapshot_written date=2026-04-26 ending_equity=10000.0 fees=0.0 gate=gate1 gross_pnl=0.0 loss_count=0 mode=live net_pnl=0.0 starting_equity=10000.0 trade_count=4 win_count=0
✓ daily snapshot written for 2026-04-26
  trades=4 net_pnl=+0.00 equity=10,000.00
