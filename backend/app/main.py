import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core.config import settings
from app.events import create_event_bus
from app.llm import require_healthy_or_raise
from app.middleware import WideEventMiddleware
from app.telemetry import configure_logging, get_logger

FRONTEND_DIR = Path(__file__).parent / "frontend"

log = get_logger("app")


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Everything stateful lives on ``app.state`` and is torn down here.

    Migrations are not run here: the template runs ``alembic upgrade head`` in
    ``scripts/prestart.sh`` before the server starts.
    """
    configure_logging(settings)
    log.info(
        "starting %s %s env=%s",
        settings.PROJECT_NAME,
        settings.VERSION,
        settings.env_name,
    )
    app.state.bus = None
    try:
        app.state.bus = create_event_bus(settings)
        app.state.llm_health = await require_healthy_or_raise(settings)
        app.state.llm_health_at = time.monotonic()
        yield
    finally:
        if app.state.bus is not None:
            await app.state.bus.close()
        log.info("stopped")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)

app.add_middleware(WideEventMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_HOST],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)
if FRONTEND_DIR.is_dir():
    # Built by `bun run build` (see backend/Dockerfile). Absent in a bare
    # backend checkout or in unit tests, where the API is all that matters.
    app.frontend("/", directory=FRONTEND_DIR)
