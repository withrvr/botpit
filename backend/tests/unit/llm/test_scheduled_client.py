"""``_ScheduledClient`` (Phase 16, DECISIONS.md §13): the transparent
wrapper ``ModelRegistry.client_for`` hands out, gating real calls through
a ``ModelAffinityScheduler`` and firing the swap counter (Phase 13) at
actual dispatch time."""

from __future__ import annotations

import asyncio

from app.llm.client import CompletionRequest, CompletionResult, Message
from app.llm.registry import _ScheduledClient
from app.llm.scheduler import ModelAffinityScheduler
from app.metrics import MODEL_SWAPS_TOTAL


class _FakeInner:
    """Minimal LLMClient: holds `complete()` open until released, so a
    test can observe the wrapper's admission gating directly."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.release_event = asyncio.Event()
        self.calls: list[str] = []

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        self.calls.append(req.model)
        await self.release_event.wait()
        return CompletionResult(
            text="{}", model=req.model, latency_ms=1, prompt_tokens=1, completion_tokens=1
        )

    async def aclose(self) -> None:
        return None


def _req(model: str) -> CompletionRequest:
    return CompletionRequest(model=model, messages=(Message(role="user", content="hi"),))


async def test_scheduled_client_delays_a_cold_model_until_a_slot_frees() -> None:
    base_url = "http://fake-scheduled-client-test:1"
    inner = _FakeInner(base_url)
    scheduler = ModelAffinityScheduler(max_loaded=1, max_wait_s=5.0)
    client = _ScheduledClient(inner, scheduler)

    task_a = asyncio.create_task(client.complete(_req("model-a")))
    await asyncio.sleep(0.02)
    assert inner.calls == ["model-a"]

    task_b = asyncio.create_task(client.complete(_req("model-b")))
    await asyncio.sleep(0.02)
    assert inner.calls == ["model-a"], "model-b should be gated, not yet dispatched"

    inner.release_event.set()
    await asyncio.wait_for(task_a, timeout=1.0)
    await asyncio.wait_for(task_b, timeout=1.0)
    assert inner.calls == ["model-a", "model-b"]


async def test_note_llm_call_fires_at_dispatch_time_through_the_wrapper() -> None:
    base_url = "http://fake-scheduled-client-swap-test:1"
    inner = _FakeInner(base_url)
    inner.release_event.set()  # complete immediately, no gating needed
    scheduler = ModelAffinityScheduler(max_loaded=2, max_wait_s=5.0)
    client = _ScheduledClient(inner, scheduler)

    before = MODEL_SWAPS_TOTAL.labels(base_url=base_url)._value.get()
    await client.complete(_req("model-a"))
    await client.complete(_req("model-a"))  # same model: not a swap
    assert MODEL_SWAPS_TOTAL.labels(base_url=base_url)._value.get() == before

    await client.complete(_req("model-b"))  # different model: a swap
    assert MODEL_SWAPS_TOTAL.labels(base_url=base_url)._value.get() == before + 1
