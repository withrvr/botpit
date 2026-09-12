from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from botpit.config import Settings
from botpit.llm import LLMHealth, LLMHealthError, probe_health, require_healthy_or_raise

Handler = Callable[[httpx.Request], httpx.Response]


def _settings(**env: str) -> Settings:
    return Settings(**env)  # type: ignore[arg-type]


def _ollama(models: list[str], *, version_fails: bool = False) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if request.url.path == "/api/version":
            if version_fails:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"version": "0.12.1"})
        return httpx.Response(404)

    return handler


def _openai(models: list[str]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models", request.url.path
        return httpx.Response(200, json={"data": [{"id": m} for m in models]})

    return handler


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ollama_all_present() -> None:
    s = _settings(llm_models="qwen3:4b,gemma3:4b")
    h = await probe_health(s, _client(_ollama(["qwen3:4b", "gemma3:4b", "other:1b"])))
    assert h.ok
    assert h.reachable
    assert h.server_version == "0.12.1"
    assert h.models_present == ["qwen3:4b", "gemma3:4b"]
    assert h.models_missing == []
    assert h.error is None
    assert h.latency_ms >= 0
    assert h.summary() == "ollama_native @ http://localhost:11434: reachable, 2/2 models present"


async def test_ollama_some_missing() -> None:
    s = _settings(llm_models="qwen3:4b,x:1,y:2,gemma3:4b")
    h = await probe_health(s, _client(_ollama(["qwen3:4b", "gemma3:4b"])))
    assert not h.ok
    assert h.reachable
    assert h.models_missing == ["x:1", "y:2"]
    assert h.summary() == (
        "ollama_native @ http://localhost:11434: reachable, 2/4 models present, missing: x:1, y:2"
    )


async def test_ollama_tagless_matches_latest() -> None:
    s = _settings(llm_models="qwen3,phi4-mini,gemma3:4b")
    h = await probe_health(s, _client(_ollama(["qwen3:latest", "phi4-mini:4b", "gemma3:latest"])))
    assert h.models_present == ["qwen3"]
    assert h.models_missing == ["phi4-mini", "gemma3:4b"]


async def test_ollama_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    h = await probe_health(_settings(), _client(handler))
    assert not h.reachable
    assert not h.ok
    assert h.error == "ConnectError: connection refused"
    assert h.models_present == []
    assert h.models_missing == _settings().llm_models
    assert h.summary() == (
        "ollama_native @ http://localhost:11434: unreachable (ConnectError: connection refused)"
    )


async def test_ollama_version_failure_tolerated() -> None:
    s = _settings(llm_models="qwen3:4b")
    h = await probe_health(s, _client(_ollama(["qwen3:4b"], version_fails=True)))
    assert h.ok
    assert h.server_version is None


async def test_ollama_http_error_is_unreachable() -> None:
    h = await probe_health(_settings(), _client(lambda _: httpx.Response(503, text="down")))
    assert not h.reachable
    assert h.error is not None
    assert h.error.startswith("HTTPStatusError")


@pytest.mark.parametrize("base", ["http://llm:8080/v1", "http://llm:8080", "http://llm:8080/v1/"])
async def test_openai_compat_base_variants(base: str) -> None:
    s = _settings(llm_provider="openai_compat", llm_base_url=base, llm_models="qwen3-4b")
    h = await probe_health(s, _client(_openai(["qwen3-4b"])))
    assert h.ok
    assert h.server_version is None
    assert h.base_url == base.rstrip("/")


async def test_openai_compat_missing_model() -> None:
    s = _settings(llm_provider="openai_compat", llm_base_url="http://llm:8080/v1", llm_models="a,b")
    h = await probe_health(s, _client(_openai(["a"])))
    assert not h.ok
    assert h.models_missing == ["b"]
    assert (
        h.summary()
        == "openai_compat @ http://llm:8080/v1: reachable, 1/2 models present, missing: b"
    )


async def test_malformed_body_is_unreachable() -> None:
    h = await probe_health(_settings(), _client(lambda _: httpx.Response(200, text="<html>")))
    assert not h.reachable
    assert h.error is not None


async def test_probe_timeout_capped() -> None:
    seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["connect"])
        return httpx.Response(200, json={"models": []})

    await probe_health(_settings(llm_timeout_seconds="120"), _client(handler))
    assert seen[0] == 5.0


async def test_require_raises_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(settings: Settings, client: object = None) -> LLMHealth:
        return LLMHealth(
            provider="ollama_native",
            base_url="http://x",
            reachable=False,
            latency_ms=1.0,
            error="ConnectError: nope",
            checked_at=datetime.now(UTC),
        )

    monkeypatch.setattr("botpit.llm.health.probe_health", fake)
    s = _settings()
    with pytest.raises(LLMHealthError) as exc:
        await require_healthy_or_raise(s, required=True)
    assert exc.value.health.error == "ConnectError: nope"
    assert str(exc.value) == "ollama_native @ http://x: unreachable (ConnectError: nope)"

    h = await require_healthy_or_raise(s, required=False)
    assert not h.ok

    assert s.llm_health_required_at_startup is False  # conftest sets it
    assert not (await require_healthy_or_raise(s)).ok
