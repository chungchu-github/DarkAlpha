# Shadow Mode Report — Gate 1 Review

**Window:** `YYYY-MM-DD` → `YYYY-MM-DD` (must be ≥60 calendar days)
**Author:** _fill in_
**Date written:** `YYYY-MM-DD`

---

## 1. Runtime health

| Metric                       | Value |
| ---------------------------- | ----- |
| Supervisor uptime (%)        |       |
| Receiver uptime (%)          |       |
| Signals received             |       |
| Signals accepted             |       |
| Signals rejected (validator) |       |
| Signals rejected (sizer)     |       |
| Signals rejected (risk gate) |       |
| Kill switch activations      |       |
| Circuit breakers tripped     |       |

## 2. Trading results

| Metric                        | Value |
| ----------------------------- | ----- |
| Trades (entries filled)       |       |
| Wins / Losses                 |       |
| Win rate (%)                  |       |
| Gross P&L (USD)               |       |
| Fees (USD)                    |       |
| Net P&L (USD)                 |       |
| Starting equity (USD)         |       |
| Ending equity (USD)           |       |
| Total return (%)              |       |
| Max intraday drawdown (%)     |       |
| Avg holding time (hours)      |       |
| Avg R realised per trade      |       |

## 3. Slippage and execution quality

- Mean entry slippage vs requested (bps):
- Mean exit slippage vs stop/TP (bps):
- Rejections caused by min notional / leverage cap:

## 4. What surprised me

_(Required — Section 10.2 of the spec. Be honest.)_

## 5. Regime / strategy breakdown

| Regime                | Trades | Net P&L | Win rate |
| --------------------- | ------ | ------- | -------- |
| vol_breakout_card     |        |         |          |
| fake_breakout_reversal|        |         |          |
| ...                   |        |         |          |

## 6. Gate 1 decision

- [ ] Expectancy positive after fees
- [ ] Max drawdown within acceptable bounds
- [ ] No unresolved critical bugs
- [ ] Risk limits never breached
- [ ] I am emotionally capable of running this with real money

**Decision:**
- [ ] Proceed to Phase 5 (live broker implementation)
- [ ] Return to Phase 2 with these changes: _______
- [ ] Shut down the system

**Rationale (1 paragraph):**

---

Signed: _______   Date: _______
