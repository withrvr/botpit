"""Liveness and dependency health for botpit's own subsystems.

The template's ``/utils/health-check/`` stays as the bare liveness probe;
these routes report the database, the event bus and the LLM backend.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.db import engine
from app.llm import LLMHealth, probe_health
from app.models import HealthReport
from app.telemetry import get_logger

log = get_logger("health")
router = APIRouter(prefix="/health", tags=["health"])


def _db_ok_sync() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # any failure means unhealthy; the reason goes to the log
        log.warning("database health check failed", exc_info=True)
        return False
    return True


@router.get("/", response_model=HealthReport)
async def health(request: Request, response: Response) -> HealthReport:
    db_ok = await run_in_threadpool(_db_ok_sync)
    bus = getattr(request.app.state, "bus", None)
    bus_ok = await bus.ping() if bus is not None else False
    llm: LLMHealth | None = getattr(request.app.state, "llm_health", None)
    healthy = db_ok and bus_ok
    if not healthy:
        response.status_code = 503
    return HealthReport(
        status="ok" if healthy else "degraded",
        version=settings.VERSION,
        env=settings.env_name,
        database="ok" if db_ok else "down",
        event_bus=f"{settings.EVENT_BUS}:{'ok' if bus_ok else 'down'}",
        llm=llm.summary() if llm else "unchecked",
    )


@router.get("/llm", response_model=LLMHealth)
async def health_llm(request: Request, response: Response) -> LLMHealth:
    result = await probe_health(settings)
    request.app.state.llm_health = result
    if not result.ok:
        response.status_code = 503
    return result
