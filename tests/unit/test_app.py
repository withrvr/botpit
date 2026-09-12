from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from botpit.app import create_app
from botpit.config import reset_settings_cache
from botpit.llm import LLMHealthError


async def _client(monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[httpx.AsyncClient, object]:
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9")  # closed port: probe fails fast
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    app = create_app()
    manager = LifespanManager(app)
    await manager.__aenter__()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return client, manager


async def test_health_ok_with_memory_bus_and_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    client, manager = await _client(monkeypatch)
    try:
        r = await client.get("/health")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["event_bus"] == "memory:ok"
        assert "unreachable" in body["llm"]
        assert "x-request-id" in r.headers
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


async def test_health_llm_reports_unreachable_as_503(monkeypatch: pytest.MonkeyPatch) -> None:
    client, manager = await _client(monkeypatch)
    try:
        r = await client.get("/health/llm")
        assert r.status_code == 503
        body = r.json()
        assert body["reachable"] is False
        assert body["provider"] == "ollama_native"
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


async def test_startup_fails_when_llm_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_HEALTH_REQUIRED_AT_STARTUP", "true")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9")
    reset_settings_cache()
    app = create_app()
    with pytest.raises(LLMHealthError, match="unreachable"):
        async with LifespanManager(app):
            pass


async def test_docs_hidden_unless_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    client, manager = await _client(monkeypatch, DEBUG="false")
    try:
        assert (await client.get("/docs")).status_code == 404
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)
