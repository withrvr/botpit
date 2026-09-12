"""Provider-agnostic LLM health probe.

Answers one question for the startup gate and the admin panel: is the backend
reachable, and are the configured models installed there?
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

if TYPE_CHECKING:
    from botpit.config import Settings

log = logging.getLogger("botpit.llm")

PROBE_TIMEOUT_CAP = 5.0


class LLMHealth(BaseModel):
    provider: str
    base_url: str
    reachable: bool
    server_version: str | None = None
    models_present: list[str] = []
    models_missing: list[str] = []
    latency_ms: float
    error: str | None = None
    checked_at: datetime

    @property
    def ok(self) -> bool:
        return self.reachable and not self.models_missing

    def summary(self) -> str:
        head = f"{self.provider} @ {self.base_url}: "
        if not self.reachable:
            return f"{head}unreachable ({self.error})"
        total = len(self.models_present) + len(self.models_missing)
        text = f"{head}reachable, {len(self.models_present)}/{total} models present"
        if self.models_missing:
            text += f", missing: {', '.join(self.models_missing)}"
        return text


class LLMHealthError(RuntimeError):
    def __init__(self, health: LLMHealth) -> None:
        super().__init__(health.summary())
        self.health = health


# A probe returns (installed model names, server version or None).
Probe = Callable[[httpx.AsyncClient, str, float], Awaitable[tuple[list[str], str | None]]]


async def _probe_ollama(
    client: httpx.AsyncClient, base: str, http_timeout: float
) -> tuple[list[str], str | None]:
    tags = await client.get(f"{base}/api/tags", timeout=http_timeout)
    tags.raise_for_status()
    names = [str(m["name"]) for m in tags.json().get("models", [])]
    version: str | None = None
    try:
        resp = await client.get(f"{base}/api/version", timeout=http_timeout)
        resp.raise_for_status()
        version = str(resp.json()["version"])
    except (httpx.HTTPError, ValueError, KeyError):
        log.debug("ollama /api/version unavailable", exc_info=True)
    return names, version


async def _probe_openai(
    client: httpx.AsyncClient, base: str, http_timeout: float
) -> tuple[list[str], str | None]:
    url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    resp = await client.get(url, timeout=http_timeout)
    resp.raise_for_status()
    return [str(m["id"]) for m in resp.json().get("data", [])], None


_PROBES: dict[str, Probe] = {"ollama_native": _probe_ollama, "openai_compat": _probe_openai}


def _is_installed(wanted: str, installed: set[str]) -> bool:
    return wanted in installed or (":" not in wanted and f"{wanted}:latest" in installed)


async def probe_health(settings: Settings, client: httpx.AsyncClient | None = None) -> LLMHealth:
    """Never raises on network trouble; ``reachable=False`` + ``error`` instead."""
    base = settings.llm_base_url.rstrip("/")
    timeout = min(settings.llm_timeout_seconds, PROBE_TIMEOUT_CAP)
    probe = _PROBES[settings.llm_provider]
    started = time.perf_counter()
    checked_at = datetime.now(UTC)
    installed: set[str] = set()
    version: str | None = None
    error: str | None = None
    try:
        if client is None:
            async with httpx.AsyncClient() as own:
                names, version = await probe(own, base, timeout)
        else:
            names, version = await probe(client, base, timeout)
        installed = set(names)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        error = f"{type(exc).__name__}: {exc}"[:200]
    latency_ms = (time.perf_counter() - started) * 1000
    present = [m for m in settings.llm_models if _is_installed(m, installed)]
    missing = [m for m in settings.llm_models if m not in present]
    return LLMHealth(
        provider=settings.llm_provider,
        base_url=base,
        reachable=error is None,
        server_version=version,
        models_present=present,
        models_missing=missing,
        latency_ms=latency_ms,
        error=error,
        checked_at=checked_at,
    )


async def require_healthy_or_raise(
    settings: Settings, *, required: bool | None = None
) -> LLMHealth:
    """Startup gate. Logs the summary; raises ``LLMHealthError`` only when required."""
    if required is None:
        required = settings.llm_health_required_at_startup
    health = await probe_health(settings)
    if health.ok:
        log.info(health.summary())
    else:
        log.warning(health.summary())
        if required:
            raise LLMHealthError(health)
    return health
