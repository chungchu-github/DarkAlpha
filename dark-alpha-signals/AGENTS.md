# Project rules for Codex

- Python 3.11
- Prefer small, readable modules and type hints.
- Always add/adjust tests when changing logic (pytest).
- Before finishing: run `pytest -q` and ensure it passes.
- Keep secrets out of repo; use `.env` + `.env.example`.
- Log key actions; avoid noisy logs in tests.
