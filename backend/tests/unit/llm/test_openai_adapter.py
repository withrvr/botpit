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
    OpenAICompatAdapter,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _response(content: str, **message_extra: object) -> dict[str, object]:
    return {
        "model": "qwen3-4b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, **message_extra},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }


def _adapter(handler: Handler, base: str = "http://llm:8080/v1") -> OpenAICompatAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatAdapter(base, client, 9.0)


def _req(**kw: object) -> CompletionRequest:
    return CompletionRequest(
        model="qwen3-4b",
        messages=(Message(role="user", content="u"),),
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "base", ["http://llm:8080/v1", "http://llm:8080", "http://llm:8080/v1/"]
)
async def test_url_and_body(base: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_response("{}"))

    schema = {"type": "object"}
    await _adapter(handler, base).complete(
        _req(
            json_schema=schema,
            seed=1,
            max_tokens=10,
            num_ctx=99,
            keep_alive="1h",
            think=True,
        )
    )
    assert seen[0].url == "http://llm:8080/v1/chat/completions"
    assert json.loads(seen[0].content) == {
        "model": "qwen3-4b",
        "messages": [{"role": "user", "content": "u"}],
        "temperature": 0.7,
        "stream": False,
        "seed": 1,
        "max_tokens": 10,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "move", "schema": schema, "strict": True},
        },
    }


async def test_body_omits_unset_keys() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_response("hi"))

    await _adapter(handler).complete(_req())
    assert json.loads(seen[0].content) == {
        "model": "qwen3-4b",
        "messages": [{"role": "user", "content": "u"}],
        "temperature": 0.7,
        "stream": False,
    }


async def test_parse_reasoning_content_field() -> None:
    body = _response("{}", reasoning_content="deep")
    result = await _adapter(lambda _: httpx.Response(200, json=body)).complete(_req())
    assert result.text == "{}"
    assert result.reasoning == "deep"
    assert result.prompt_tokens == 5
    assert result.completion_tokens == 3
    assert result.finish_reason == "stop"
    assert result.model == "qwen3-4b"


async def test_parse_reasoning_field() -> None:
    body = _response("{}", reasoning="r")
    result = await _adapter(lambda _: httpx.Response(200, json=body)).complete(_req())
    assert result.reasoning == "r"


async def test_parse_inline_think_tags() -> None:
    body = _response("<think>why</think>\n{}")
    result = await _adapter(lambda _: httpx.Response(200, json=body)).complete(_req())
    assert result.text == "{}"
    assert result.reasoning == "why"


async def test_null_content_and_no_usage() -> None:
    body = {"choices": [{"message": {"content": None}}]}
    result = await _adapter(lambda _: httpx.Response(200, json=body)).complete(_req())
    assert result.text == ""
    assert result.reasoning is None
    assert result.prompt_tokens is None
    assert result.finish_reason is None


@pytest.mark.parametrize(
    ("status", "retriable"), [(503, True), (429, True), (422, False)]
)
async def test_http_status_mapping(status: int, retriable: bool) -> None:
    with pytest.raises(LLMError) as info:
        await _adapter(lambda _: httpx.Response(status, text="x")).complete(_req())
    assert info.value.retriable is retriable


async def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(LLMTimeoutError):
        await _adapter(handler).complete(_req())


async def test_bad_json_and_empty_choices() -> None:
    with pytest.raises(LLMBadResponseError):
        await _adapter(lambda _: httpx.Response(200, text="nope")).complete(_req())
    with pytest.raises(LLMBadResponseError):
        await _adapter(lambda _: httpx.Response(200, json={"choices": []})).complete(
            _req()
        )
