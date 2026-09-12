"""Dependency health for botpit's own subsystems.

``GET /health/`` is a cheap, unauthenticated readiness probe (database, event
bus, last known LLM state, no outbound calls). ``GET /health/llm`` re-probes
the LLM backend and is superuser-only: it makes outbound requests and reports
the configured base URL and model list, which is admin information.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_active_superuser
from app.core.config import settings
from app.core.db import engine
from app.llm import LLMHealth, probe_health
from app.models import HealthReport
from app.telemetry import get_logger

log = get_logger("health")
router = APIRouter(prefix="/health", tags=["health"])

LLM_PROBE_TTL_SECONDS = 10.0


def _db_ok_sync() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # any failure means unhealthy; the reason goes to the log
        log.warning("database health check failed", exc_info=True)
        return False
    return True


def _llm_summary(llm: LLMHealth | None) -> str:
    if llm is None:
        return "unchecked"
    if not llm.reachable:
        return "unreachable"
    if llm.models_missing:
        return f"degraded ({len(llm.models_present)}/{len(settings.LLM_MODELS)} models)"
    return "ok"


async def probe_llm_cached(state: Any) -> LLMHealth:
    """One outbound probe per TTL per worker, whoever asks."""
    now = time.monotonic()
    cached: LLMHealth | None = getattr(state, "llm_health", None)
    stamp: float = getattr(state, "llm_health_at", 0.0)
    if cached is not None and now - stamp < LLM_PROBE_TTL_SECONDS:
        return cached
    result = await probe_health(settings)
    state.llm_health = result
    state.llm_health_at = now
    return result


@router.get("/", response_model=HealthReport)
async def health(request: Request, response: Response) -> HealthReport:
    db_ok = await run_in_threadpool(_db_ok_sync)
    bus = getattr(request.app.state, "bus", None)
    bus_ok = await bus.ping() if bus is not None else False
    healthy = db_ok and bus_ok
    if not healthy:
        response.status_code = 503
    return HealthReport(
        status="ok" if healthy else "degraded",
        version=settings.VERSION,
        env=settings.env_name,
        database="ok" if db_ok else "down",
        event_bus=f"{settings.EVENT_BUS}:{'ok' if bus_ok else 'down'}",
        llm=_llm_summary(getattr(request.app.state, "llm_health", None)),
    )


@router.get(
    "/llm",
    response_model=LLMHealth,
    dependencies=[Depends(get_current_active_superuser)],
)
async def health_llm(request: Request, response: Response) -> LLMHealth:
    result = await probe_llm_cached(request.app.state)
    if not result.ok:
        response.status_code = 503
    return result
