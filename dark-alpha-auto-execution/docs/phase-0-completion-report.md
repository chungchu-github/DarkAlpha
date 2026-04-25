# Phase 0 Completion Report

> **Fill in the "Reviewer notes" section before proceeding to Phase 1.**
> Phase 1 must NOT start without user sign-off below.

---

## Checklist

- [ ] `pytest` passes with ≥80% coverage on `signal_adapter/`
- [ ] `signal_adapter/receiver.py` successfully receives and stores a sample ProposalCard payload to SQLite
- [ ] SQLite DB initializes with all 4 tables on first run
- [ ] `ruff check` passes with zero errors
- [ ] `mypy src/` passes with zero errors
- [ ] CI workflow file committed and green on first push
- [ ] `.env.example` documents all required variables
- [ ] Config YAML files (main, validator, sizer × 3, risk_gate, breakers) committed

---

## Test Results

```
# Paste pytest output here
$ poetry run pytest -q --cov=src --cov-fail-under=80
```

---

## Smoke Test: Signal Adapter

Verify the receiver works end-to-end:

```bash
# Terminal 1 — start the receiver
poetry run uvicorn signal_adapter.receiver:app --port 8765

# Terminal 2 — send a sample ProposalCard
curl -s -X POST http://127.0.0.1:8765/signal \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_proposal_card.json

# Expected response:
# {"event_id":"abc123def456","symbol":"BTCUSDT-PERP"}

# Terminal 2 — verify SQLite write
sqlite3 data/shadow.db "SELECT event_id, symbol, setup_type FROM setup_events;"
```

---

## Files Created

| File | Status |
|---|---|
| `pyproject.toml` | ☐ |
| `.env.example` | ☐ |
| `.pre-commit-config.yaml` | ☐ |
| `config/main.yaml` | ☐ |
| `config/validator.yaml` | ☐ |
| `config/sizer.gate1.yaml` | ☐ |
| `config/sizer.gate2.yaml` | ☐ |
| `config/sizer.gate3.yaml` | ☐ |
| `config/risk_gate.yaml` | ☐ |
| `config/breakers.yaml` | ☐ |
| `src/signal_adapter/schemas.py` | ☐ |
| `src/signal_adapter/translator.py` | ☐ |
| `src/signal_adapter/receiver.py` | ☐ |
| `src/storage/db.py` | ☐ |
| `src/storage/migrations/001_init.sql` | ☐ |
| `src/observability/logging.py` | ☐ |
| `tests/unit/test_translator.py` | ☐ |
| `tests/fixtures/sample_proposal_card.json` | ☐ |
| `.github/workflows/ci.yml` | ☐ |

---

## Reviewer Notes

*Date reviewed:*

*Reviewed by:*

*Notes / issues found:*

---

## Sign-off

By signing below, you confirm Phase 0 acceptance criteria are met and authorize Phase 1 to begin.

**Signed:** ________________________________  
**Date:** ____________________________________

> Phase 1 scope: `kill_switch` (3 trigger methods), `circuit_breaker` (all 5 rules configurable),
> `audit` trail module, and at least one alert channel (Telegram or email).
