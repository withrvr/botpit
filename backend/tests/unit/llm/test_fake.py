from __future__ import annotations

import pytest

from app.llm import CompletionRequest, FakeLLMClient, LLMError, LLMTimeoutError, Message
from app.telemetry import wide_event


def _req(text: str = "hi") -> CompletionRequest:
    return CompletionRequest(
        model="fake", messages=(Message(role="user", content=text),)
    )


async def test_canned_responses_and_exceptions() -> None:
    fake = FakeLLMClient(["one", LLMTimeoutError("slow"), "<think>t</think>two"])
    with wide_event("test") as ev:
        first = await fake.complete(_req("a"))
        with pytest.raises(LLMTimeoutError):
            await fake.complete(_req("b"))
        third = await fake.complete(_req("c"))
    assert first.text == "one"
    assert first.model == "fake"
    assert third.text == "two"
    assert third.reasoning == "t"
    assert [r.messages[0].content for r in fake.requests] == ["a", "b", "c"]
    assert [c["ok"] for c in ev["llm.calls"]] == [True, False, True]
    with pytest.raises(LLMError, match="no responses left"):
        await fake.complete(_req())


async def test_callable_and_latency() -> None:
    fake = FakeLLMClient(lambda req: req.messages[0].content.upper(), latency_s=0.001)
    result = await fake.complete(_req("pong"))
    assert result.text == "PONG"
    assert result.latency_ms == 1
    await fake.aclose()
