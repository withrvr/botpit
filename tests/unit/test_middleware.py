from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from fastapi import FastAPI

from botpit.middleware import REQUEST_ID_HEADER, WideEventMiddleware

if TYPE_CHECKING:
    import pytest


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(WideEventMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"hi": "there"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _wide(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [r.wide for r in caplog.records if r.name == "botpit.wide"]


async def test_request_emits_wide_event_and_header(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="botpit.wide")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://t"
    ) as c:
        r = await c.get("/ok?x=1", headers={REQUEST_ID_HEADER: "rid-123"})
    assert r.headers[REQUEST_ID_HEADER] == "rid-123"
    (ev,) = _wide(caplog)
    assert ev["event_kind"] == "http.request"
    assert ev["request_id"] == "rid-123"
    assert ev["http_status"] == 200
    assert ev["http_query"] == "x=1"
    assert ev["outcome"] == "ok"


async def test_exception_marks_error_and_propagates(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="botpit.wide")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        r = await c.get("/boom")
    assert r.status_code == 500
    (ev,) = _wide(caplog)
    assert ev["outcome"] == "error"
    assert ev["error.type"] == "RuntimeError"


async def test_health_is_not_logged_but_gets_id(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="botpit.wide")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://t"
    ) as c:
        r = await c.get("/health")
    assert len(r.headers[REQUEST_ID_HEADER]) == 32
    assert _wide(caplog) == []
