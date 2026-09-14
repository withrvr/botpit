"""In-memory ``LLMClient`` for tests: canned responses or a callable, records requests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from app.llm.client import (
    CompletionRequest,
    CompletionResult,
    LLMError,
    record_llm_call,
    strip_think_tags,
)


class FakeLLMClient:
    def __init__(
        self,
        responses: Sequence[str | Exception] | Callable[[CompletionRequest], str] = (),
        *,
        latency_s: float = 0.0,
    ) -> None:
        self._fn: Callable[[CompletionRequest], str] | None = None
        self._queue: list[str | Exception] = []
        if isinstance(responses, Sequence):
            self._queue = list(responses)
        else:
            self._fn = responses
        self.latency_s = latency_s
        self.requests: list[CompletionRequest] = []
        self.base_url = "fake://test"

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        self.requests.append(req)
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        latency_ms = int(self.latency_s * 1000)
        if self._fn is not None:
            raw: str | Exception = self._fn(req)
        elif self._queue:
            raw = self._queue.pop(0)
        else:
            raw = LLMError("FakeLLMClient: no responses left")
        if isinstance(raw, Exception):
            record_llm_call(req.model, latency_ms, error=raw)
            raise raw
        text, reasoning = strip_think_tags(raw)
        record_llm_call(req.model, latency_ms)
        return CompletionResult(
            text=text,
            model=req.model,
            latency_ms=latency_ms,
            prompt_tokens=None,
            completion_tokens=None,
            reasoning=reasoning,
        )

    async def aclose(self) -> None:
        return None
