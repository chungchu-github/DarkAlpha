"""Localhost-only access guard.

The dashboard exposes operational state (kill-switch, positions, halts).
It is intended for personal use on the operator's machine only — running
on port 8766 without auth — so this middleware refuses any request whose
client.host is not loopback.

This is *not* sufficient hardening if the host is shared, on a LAN with
untrusted devices, or behind any proxy that rewrites client.host. It is
the right level of effort for a single-operator localhost trading bot.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class LocalhostOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        client = request.client
        host = client.host if client is not None else None
        if host not in _LOCALHOST_HOSTS:
            return PlainTextResponse(
                f"403 Forbidden — dashboard is localhost-only (got host={host!r})",
                status_code=403,
            )
        return await call_next(request)
