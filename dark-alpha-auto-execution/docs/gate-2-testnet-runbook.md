# Gate 2 Testnet Runbook

## Preconditions

1. Configure `BINANCE_FUTURES_TESTNET_API_KEY`.
2. Configure `BINANCE_FUTURES_TESTNET_API_SECRET`.
3. Keep `live.environment: testnet`.
4. Keep `live.allow_mainnet: false`.
5. Confirm `docs/gate-2-authorization.md` exists.

## Manual Testnet Exercise

Preferred helper flow:

1. Inspect exchange filters:

   ```bash
   poetry run dark-alpha gate2-test filters --symbol ETHUSDT-PERP
   ```

2. Generate a dry-run payload:

   ```bash
   poetry run dark-alpha gate2-test bracket --symbol ETHUSDT-PERP
   ```

3. Submit through the normal receiver path:

   ```bash
   poetry run dark-alpha gate2-test bracket --symbol ETHUSDT-PERP --submit
   ```

4. Generate the test report:

   ```bash
   poetry run dark-alpha gate2-test report --ticket-id <ticket_id>
   ```

Lower-level manual flow:

1. Confirm no stale local state:

   ```bash
   poetry run dark-alpha reconcile-live --symbol BTCUSDT-PERP
   ```

2. Temporarily set `mode: live` in `config/main.yaml` for the testnet window.

3. Submit one tiny Gate 2 ticket through the normal signal receiver path.

4. Poll order state:

   ```bash
   poetry run dark-alpha sync-live-orders --symbol BTCUSDT-PERP
   ```

5. Reconcile after each state transition:

   ```bash
   poetry run dark-alpha reconcile-live --symbol BTCUSDT-PERP
   ```

6. If anything looks wrong:

   ```bash
   poetry run dark-alpha halt --reason "gate2 testnet anomaly"
   poetry run dark-alpha cancel-open-orders --symbol BTCUSDT-PERP --yes
   poetry run dark-alpha flatten --symbol BTCUSDT-PERP --yes
   ```

7. Return `config/main.yaml` to `mode: shadow`.

## Expected Local State

- Entry `PARTIALLY_FILLED` creates or updates a live `positions.status='partial'` row.
- Entry `FILLED` creates or updates a live `positions.status='open'` row.
- Stop `FILLED` closes the live position with `exit_reason='stop_loss'`.
- Take-profit `FILLED` closes the live position with `exit_reason='take_profit'`.
- Reconciliation mismatch activates the kill switch.
- `gate2-test bracket` uses Binance testnet exchange filters before producing prices.

## Mainnet

Mainnet is not part of Gate 2.
