"""FastAPI app for the localhost-only monitoring dashboard.

Run:
    poetry run uvicorn dashboard.app:app --port 8766 --host 127.0.0.1

Or use ``scripts/run_dashboard.sh``.

The app is intentionally separate from the signal receiver on port 8765
so that restarting the dashboard never disturbs the signal hot path.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

# bootstrap loads .env before anything reads os.getenv
import bootstrap  # noqa: F401  — must precede any module that calls os.getenv at import time

from .middleware import LocalhostOnlyMiddleware
from .routes import router as api_router

_INDEX_HTML = Path(__file__).resolve().parent / "static" / "index.html"

app = FastAPI(title="Dark Alpha Live Monitor", version="0.1.0")
app.add_middleware(LocalhostOnlyMiddleware)
app.include_router(api_router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_INDEX_HTML, media_type="text/html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
