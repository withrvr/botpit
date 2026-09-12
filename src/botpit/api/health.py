"""Liveness and dependency health."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from botpit.config import get_settings
from botpit.db import get_engine
from botpit.llm import LLMHealth, probe_health

router = APIRouter(tags=["health"])


class HealthReport(BaseModel):
    status: str
    version: str
    env: str
    database: str
    event_bus: str
    llm: str


async def _db_ok() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # any failure means unhealthy
        return False
    return True


@router.get("/health", response_model=HealthReport)
async def health(request: Request, response: Response) -> HealthReport:
    settings = get_settings()
    db_ok = await _db_ok()
    bus_ok = await request.app.state.bus.ping()
    llm: LLMHealth | None = getattr(request.app.state, "llm_health", None)
    healthy = db_ok and bus_ok
    if not healthy:
        response.status_code = 503
    return HealthReport(
        status="ok" if healthy else "degraded",
        version=settings.service_version,
        env=settings.env,
        database="ok" if db_ok else "down",
        event_bus=f"{settings.event_bus}:{'ok' if bus_ok else 'down'}",
        llm=llm.summary() if llm else "unchecked",
    )


@router.get("/health/llm", response_model=LLMHealth)
async def health_llm(request: Request, response: Response) -> LLMHealth:
    result = await probe_health(get_settings())
    request.app.state.llm_health = result
    if not result.ok:
        response.status_code = 503
    return result


def as_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
