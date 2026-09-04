"""ASGI middleware for the Phase 13 "sensible request-size limits" gate.

In a real deployment the reverse proxy or load balancer in front of this API
is the primary defense against an oversized request -- it can reject one
without this process ever seeing the connection. This middleware is the
backstop for whatever reaches the app directly (local development, a test
client, a proxy configured without its own limit), not a replacement for that
layer.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import error_body


class MaxBodySizeMiddleware:
    """Rejects a request whose declared Content-Length exceeds `max_bytes`,
    before the body is read or the route handler runs.

    Content-Length only: every request this API accepts is a small JSON
    object with no legitimate reason to be chunked, so a declared-length
    check is sufficient. A request with no Content-Length (or a chunked
    transfer) is not blocked here -- Starlette still caps how much it will
    buffer while parsing JSON, and this middleware is a backstop, not the
    only layer.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    too_large = int(value) > self.max_bytes
                except ValueError:
                    too_large = False
                if too_large:
                    await _too_large_response(self.max_bytes, send)
                    return
                break

        await self.app(scope, receive, send)


async def _too_large_response(max_bytes: int, send: Send) -> None:
    body = json.dumps(
        error_body(
            "request_too_large",
            f"Request body exceeds the {max_bytes}-byte limit",
        )
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
