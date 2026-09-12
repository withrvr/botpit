"""ASGI application factory.

``uvicorn botpit.app:create_app --factory``. Everything stateful (event bus,
LLM health, DB engine) is created in the lifespan and hung on ``app.state``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from botpit import __version__
from botpit.api import health
from botpit.config import get_settings
from botpit.db import dispose_engine, upgrade_to_head
from botpit.events import create_event_bus
from botpit.llm import require_healthy_or_raise
from botpit.logging import configure_logging, get_logger
from botpit.middleware import WideEventMiddleware

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    log.info("starting %s %s env=%s", settings.app_name, __version__, settings.env)
    app.state.bus = None
    try:
        await upgrade_to_head()
        app.state.bus = create_event_bus(settings)
        app.state.llm_health = await require_healthy_or_raise(settings)
        yield
    finally:
        if app.state.bus is not None:
            await app.state.bus.close()
        await dispose_engine()
        log.info("stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        debug=settings.debug,
        docs_url="/docs" if settings.debug else None,
    )
    app.add_middleware(WideEventMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app
