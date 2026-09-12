from __future__ import annotations

import pytest

from app.llm import (
    LLMBadResponseError,
    LLMError,
    LLMTimeoutError,
    strip_think_tags,
    with_retries,
)


def test_strip_think_tags_with_block() -> None:
    text, reasoning = strip_think_tags('<think>\nhmm\n</think>\n{"a": 1}')
    assert text == '{"a": 1}'
    assert reasoning == "hmm"


def test_strip_think_tags_without_block() -> None:
    assert strip_think_tags('{"a": 1}') == ('{"a": 1}', None)


def test_strip_think_tags_joins_multiple_blocks() -> None:
    text, reasoning = strip_think_tags("<think>a</think>x<think>b</think>y")
    assert text == "xy"
    assert reasoning == "a\nb"


class _Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def test_with_retries_retries_retriable_then_succeeds() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise LLMTimeoutError("slow")
        return "ok"

    sleeps = _Sleeps()
    assert await with_retries(fn, sleep=sleeps) == "ok"
    assert calls == 3
    assert sleeps.delays == [0.2, 0.4]


async def test_with_retries_gives_up_after_attempts() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise LLMError("boom", retriable=True)

    sleeps = _Sleeps()
    with pytest.raises(LLMError):
        await with_retries(fn, attempts=4, base_delay=1.0, sleep=sleeps)
    assert calls == 4
    assert sleeps.delays == [1.0, 2.0, 4.0]


async def test_with_retries_does_not_retry_non_retriable() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise LLMBadResponseError("bad")

    sleeps = _Sleeps()
    with pytest.raises(LLMBadResponseError):
        await with_retries(fn, sleep=sleeps)
    assert calls == 1
    assert sleeps.delays == []
