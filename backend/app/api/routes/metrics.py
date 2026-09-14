"""``GET /metrics`` (Phase 13, DECISIONS.md §14): Prometheus text
exposition of ``app.metrics.REGISTRY``. Unauthenticated, matching
``GET /health`` -- both are operational endpoints meant for a scraper/
monitor, not user data, and Prometheus scrape configs rarely carry a
bearer token easily."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.metrics import REGISTRY

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
