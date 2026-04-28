# INVALID — first 24h burn-in

This run is filed as **invalid evidence** for Gate 6.7. The 24h window
itself completed (snapshots 001–024 + report.md present, kill switch
clear throughout, reconcile-live status=ok every snapshot), but it was
inherited a dirty operational state at start: a pre-existing
unprotected live position on Binance testnet from a Gate 2 manual test
ticket whose protective bracket was rejected by the exchange before the
broker routing fix landed.

The full root cause and resolution are recorded in
`docs/incidents/2026-04-26-bracket-reject-orphan-position.md`.

The Gate 6.8 three-clean counter remains at **0 / 3**. The next attempt
must:

1. Wait for the incident's fix to land (single commit covering
   `LiveReconciler` guard wiring + `burn-in.sh` clean-state pre-flight).
2. Manually close the testnet orphan ticket
   `01KQ47CFJB18G3J9A3PE3EZWFR` (place matching stop + take_profit algo
   orders or `dark-alpha emergency-close` after reconcile clears).
3. Confirm `reconcile-live` returns `status=ok` with no kill-switch
   activation.
4. Re-run `BURN_IN_HOURS=24 ./scripts/burn-in.sh` — the new pre-flight
   should accept a clean DB.

This directory is preserved as evidence of the discovery, not as
qualifying burn-in evidence.
