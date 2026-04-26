# Gate 6 Mainnet Micro-Live Authorization

This file must be copied to `docs/gate-6-authorization.md` and filled before
any mainnet Gate 6 exercise.

## Operator Limits

- Authorized symbol:
- Direction allowed: long / short / both
- Strategy allowed: manual test signal / existing strategy
- Max notional per order USDT:
- Max leverage:
- Max concurrent positions: 1
- Max daily loss USDT:
- Exercise window start UTC:
- Exercise window end UTC:
- Auto cancel-all after window: yes
- Auto flatten after window: yes / manual confirm

## Safety Acknowledgement

- [ ] Mainnet key is dedicated to this bot.
- [ ] Mainnet key has no withdrawal permission.
- [ ] Mainnet key is IP restricted when possible.
- [ ] `poetry run dark-alpha gate-check all` passed immediately before the run.
- [ ] Binance account has no unknown open orders.
- [ ] Binance account has no unknown position.
- [ ] Every live ticket must include stop loss and take profit.
- [ ] Operator accepts that this is a micro-live canary, not production live trading.

## Signature

- Operator:
- Date:
- Notes:
