"""Dashboard JSON endpoints — one route per panel.

All routes are read-only. The page itself is served by ``app.py`` as a
static FileResponse; this module only handles the per-panel /api/* JSON.
Each handler is a one-liner over ``queries.X()`` so the route layer adds
no logic and stays trivially testable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import queries

router = APIRouter(prefix="/api")


@router.get("/kpis")
def get_kpis() -> dict[str, Any]:
    return queries.kpis()


@router.get("/positions")
def get_positions() -> list[dict[str, Any]]:
    return queries.live_positions()


@router.get("/tickets")
def get_tickets() -> list[dict[str, Any]]:
    return queries.recent_tickets()


@router.get("/reconcile")
def get_reconcile() -> list[dict[str, Any]]:
    return queries.reconcile_history()


@router.get("/heartbeat")
def get_heartbeat() -> dict[str, Any]:
    return queries.user_stream_heartbeat()


@router.get("/gate6")
def get_gate6() -> dict[str, Any]:
    return queries.gate6_readiness()


@router.get("/breakers")
def get_breakers() -> list[dict[str, Any]]:
    return queries.circuit_breakers()


@router.get("/halts")
def get_halts() -> list[dict[str, Any]]:
    return queries.recent_halts()


@router.get("/equity")
def get_equity() -> list[dict[str, Any]]:
    return queries.equity_sparkline()
