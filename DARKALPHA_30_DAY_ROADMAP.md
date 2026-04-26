# DarkAlpha 30 Day Transformation Roadmap

Status date: 2026-04-26

## Current Gate

- Current maturity: P3+ shadow / paper trading with Gate 2 testnet broker and Gate 2.5 -> 6 safety checks
- Live auto trading: mainnet not allowed by default
- Allowed mode: signal proposal + shadow execution + gated testnet broker testing + deterministic Gate 2.5 -> 6 checks

## Week 1: Data Source And Health Gate

Goal: stale or degraded market data must not create signals or downstream tickets.

- [x] Add a unified market health gate before signal generation.
- [x] Block stale price data.
- [x] Block stale kline receive data.
- [x] Block stale or missing funding data.
- [x] Block stale or missing open interest data.
- [x] Attach `data_health` to proposal card payloads.
- [x] Show data health in Telegram message/detail output.
- [x] Preserve `data_health` through the auto-execution signal adapter.
- [x] Reject unhealthy payloads in the auto-execution validator.
- [x] Add regression tests for price/kline/OI stale blocking.

Remaining:

- [ ] Add explicit clock degraded blocking policy.
- [ ] Emit critical Telegram alert when data health stays blocked for N minutes.
- [ ] Add per-symbol health summary command/report.

## Week 2: Risk Control And Trading Journal

Goal: every accepted signal must have auditable risk and post-signal outcome tracking.

- [x] Add signal journal persistence.
- [x] Create 5m / 15m / 1h / 4h post-signal outcome placeholders.
- [x] Implement outcome evaluator that fills pending 5m / 15m / 1h / 4h rows.
- [x] Add strategy-level daily ticket cap.
- [x] Add symbol-level daily ticket cap.
- [x] Add max consecutive loss gate from closed paper positions.
- [x] Add weekly loss cap.
- [x] Add hard leverage display cap in signal layer.
- [x] Add `risk_level`, `invalid_condition`, and `take_profit` to ProposalCard.

## Week 3: Paper Trading And Backtest

Goal: paper trading should approximate executable behavior, not instantly assume fills.

- [x] Change paper broker entry from immediate fill to touch/TTL fill.
- [x] Persist expired unfilled tickets.
- [x] Add strategy/symbol/regime performance report.
- [x] Add basic historical backtest runner.
- [x] Compare backtest vs shadow results.

## Week 4: Micro Live Readiness

Goal: prepare live trading design, but keep live disabled until shadow metrics pass.

- [x] Write live broker spec.
- [x] Add client order id format.
- [x] Add idempotency table/check.
- [x] Add position reconciliation design.
- [x] Add cancel-all and emergency-close design.
- [x] Add testnet/mainnet isolation plan.
- [x] Define Gate 2 authorization checklist.

Closeout:

- Mainnet order submission is still disabled.
- Gate 2 testnet broker implementation has started.
- `ModeRouter` can dispatch `shadow_mode=false` tickets only after live mode and Gate 2 preflight pass.
- Mainnet is blocked unless explicitly enabled and Gate 2 authorization exists.
- Next work must use `docs/week-4-closeout.md` as the handoff checklist.

## Phase 5: Gate 2 Testnet Broker

Goal: enable controlled Binance Futures testnet execution without weakening shadow-mode safeguards.

- [x] Add Binance Futures testnet signed REST client.
- [x] Add testnet-only broker adapter.
- [x] Submit entry, stop, and take-profit orders with deterministic client order ids.
- [x] Block existing exchange position before entry.
- [x] Block existing exchange open orders before entry.
- [x] Cancel all symbol orders if bracket submission partially fails.
- [x] Record live order acknowledgements into local `orders`.
- [x] Mark idempotency records as submitted after exchange acknowledgement.
- [x] Add testnet cancel-all CLI command.
- [x] Add testnet reduce-only emergency flatten command.
- [x] Add startup reconciliation that compares exchange positions/orders with local DB.
- [x] Add order status polling into local `orders` / `order_idempotency`.
- [x] Halt on reconciliation mismatch.
- [x] Create local live position rows only after confirmed exchange fill.
- [x] Convert partial fills into explicit live position lifecycle state.
- [x] Add Telegram critical alert path through kill switch on reconciliation mismatch.
- [x] Add Gate 2 testnet authorization file.
- [x] Add Gate 2 testnet runbook.
- [x] Run dry Gate 2 testnet exercise with tiny quantities using real testnet credentials.
- [x] Add Gate 2 test report generator.
- [x] Add `gate2-test bracket` helper command.
- [x] Add Binance exchange filter parsing for tickSize, stepSize, minQty, and minNotional.
- [x] Apply exchange filters before submitting testnet broker orders.
- [x] Add deterministic Gate 2.5 fill lifecycle check for entry partial/full, SL/TP close, cancel/reconcile, and emergency flatten.

## Gate 2.5 -> Gate 6 Compression

Goal: compress the remaining live-readiness gates into executable checks without silently enabling mainnet.

- [x] Gate 2.5: `dark-alpha gate-check gate25` validates live fill lifecycle locally.
- [x] Gate 3: `dark-alpha gate-check gate3` validates restart/duplicate/reconcile/kill-switch behavior locally.
- [x] Gate 3.5: `dark-alpha gate-check gate35` validates key risk rejection paths locally.
- [x] Gate 5: `dark-alpha gate-check gate5` validates mainnet remains locked without explicit runtime controls.
- [x] Gate 6: `dark-alpha gate-check gate6` validates the micro-live canary scaffold and caps logic locally.
- [x] `dark-alpha gate-check all` runs the full deterministic suite.
- [x] Generic Binance Futures broker scaffold exists, while `BinanceTestnetBroker` remains as the testnet-only compatibility wrapper.
- [x] Mainnet preflight requires dedicated `BINANCE_FUTURES_MAINNET_API_KEY` / `BINANCE_FUTURES_MAINNET_API_SECRET`.
- [x] Mainnet micro-live requires allowed symbols, max notional, max leverage, daily loss cap, one-position cap, SL, TP, and an exercise window.
- [x] Submit path blocks existing exchange algo orders before placing a new bracket.
- [x] `dark-alpha gate6 authorize` generates a concrete Gate 6 authorization file and matching config block.
- [x] `dark-alpha gate6 preflight` checks mainnet account cleanliness before the canary.
- [x] `dark-alpha gate6 closeout` runs cancel-all, flatten, sync, reconcile, and writes a closeout report.

Remaining before a real Gate 6 order:

- [ ] Fill `config/main.yaml` `live.micro_live` with the operator-approved symbol/window/caps.
- [ ] Create `docs/gate-6-authorization.md` with exact limits using `dark-alpha gate6 authorize`.
- [ ] Run `dark-alpha gate-check all` immediately before the exercise.
- [ ] Run `dark-alpha gate6 preflight` immediately before the exercise.
- [ ] Start live services only inside the approved exercise window.
- [ ] After the exercise, run sync, reconcile, cancel-all, flatten if needed, generate report, and return `mode: shadow`.

## Non-Negotiable Live Preconditions

Live mode remains blocked until all are true:

- Shadow mode has at least 60 calendar days of clean operation.
- Signal journal has outcome tracking by strategy and symbol.
- Max drawdown, win rate, expectancy, and consecutive losses are known.
- Live broker has idempotency, client order ids, reconciliation, cancel-all, and emergency close.
- API keys are rotated and separated by environment.
- Every live ticket has stop loss, take profit, max loss, size, leverage cap, and reduce-only exits.
