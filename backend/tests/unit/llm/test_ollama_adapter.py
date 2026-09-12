from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.llm import (
    CompletionRequest,
    LLMBadResponseError,
    LLMError,
    LLMTimeoutError,
    Message,
    OllamaNativeAdapter,
)
from app.telemetry import wide_event

Handler = Callable[[httpx.Request], httpx.Response]

OK_RESPONSE = {
    "model": "qwen3:4b",
    "message": {
        "role": "assistant",
        "content": '{"answer": "pong"}',
        "thinking": "ping?",
    },
    "done_reason": "stop",
    "prompt_eval_count": 12,
    "eval_count": 7,
}


def _adapter(handler: Handler, **kw: object) -> OllamaNativeAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaNativeAdapter("http://ollama:11434/", client, 9.0, **kw)  # type: ignore[arg-type]


def _req(**kw: object) -> CompletionRequest:
    return CompletionRequest(
        model="qwen3:4b",
        messages=(
            Message(role="system", content="s"),
            Message(role="user", content="u"),
        ),
        **kw,  # type: ignore[arg-type]
    )


async def test_request_body_minimal() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_RESPONSE)

    await _adapter(handler).complete(_req())
    assert seen[0].url == "http://ollama:11434/api/chat"
    assert json.loads(seen[0].content) == {
        "model": "qwen3:4b",
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ],
        "stream": False,
        "options": {"temperature": 0.7},
    }


async def test_request_body_full() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_RESPONSE)

    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    await _adapter(handler, num_ctx=2048, keep_alive="5m").complete(
        _req(
            json_schema=schema,
            temperature=0.1,
            seed=42,
            max_tokens=64,
            think=False,
            num_ctx=8192,
        )
    )
    assert json.loads(seen[0].content) == {
        "model": "qwen3:4b",
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ],
        "stream": False,
        "format": schema,
        "options": {"temperature": 0.1, "seed": 42, "num_predict": 64, "num_ctx": 8192},
        "keep_alive": "5m",
        "think": False,
    }


async def test_success_parse_and_metrics() -> None:
    with wide_event("test") as ev:
        result = await _adapter(
            lambda _: httpx.Response(200, json=OK_RESPONSE)
        ).complete(_req())
    assert result.text == '{"answer": "pong"}'
    assert result.reasoning == "ping?"
    assert result.model == "qwen3:4b"
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 7
    assert result.finish_reason == "stop"
    assert result.latency_ms >= 0
    (call,) = ev["llm.calls"]
    assert call["model"] == "qwen3:4b"
    assert call["ok"] is True
    assert call["prompt_tokens"] == 12
    assert call["error"] is None


async def test_inline_think_tags_fallback() -> None:
    body = {"message": {"content": "<think>x</think>{}"}}
    result = await _adapter(lambda _: httpx.Response(200, json=body)).complete(_req())
    assert result.text == "{}"
    assert result.reasoning == "x"
    assert result.prompt_tokens is None


@pytest.mark.parametrize(
    ("status", "retriable"), [(500, True), (429, True), (400, False)]
)
async def test_http_status_mapping(status: int, retriable: bool) -> None:
    with wide_event("test") as ev, pytest.raises(LLMError) as info:
        await _adapter(lambda _: httpx.Response(status, text="nope")).complete(_req())
    assert info.value.retriable is retriable
    assert f"HTTP {status}" in str(info.value)
    assert ev["llm.calls"][0]["ok"] is False
    assert ev["llm.calls"][0]["error"] == "LLMError"


async def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMTimeoutError) as info:
        await _adapter(handler).complete(_req())
    assert info.value.retriable


async def test_connection_error_is_retriable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMError) as info:
        await _adapter(handler).complete(_req())
    assert info.value.retriable
    assert not isinstance(info.value, LLMTimeoutError)


async def test_bad_json() -> None:
    with pytest.raises(LLMBadResponseError) as info:
        await _adapter(lambda _: httpx.Response(200, text="<html>")).complete(_req())
    assert not info.value.retriable


async def test_missing_keys_is_bad_response() -> None:
    with pytest.raises(LLMBadResponseError):
        await _adapter(lambda _: httpx.Response(200, json={"done": True})).complete(
            _req()
        )
