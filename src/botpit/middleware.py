"""Per-request wide event.

Replaces uvicorn's access log with one structured event per HTTP request.
Websocket sessions get their own ``ws.session`` event in the websocket layer.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from botpit.logging import wide_event

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

REQUEST_ID_HEADER = "x-request-id"
SKIP_PATHS = frozenset({"/health", "/health/llm", "/metrics"})


class WideEventMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        if request.url.path in SKIP_PATHS:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        with wide_event(
            "http.request",
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
            http_query=request.url.query or None,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        ) as ev:
            response = await call_next(request)
            ev.set(http_status=response.status_code)
            if response.status_code >= 500:
                ev.set(**{"error.type": "http_5xx", "force_keep": True})
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
