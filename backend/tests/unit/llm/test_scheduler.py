"""ModelAffinityScheduler (Phase 16, DECISIONS.md §13): bounded admission
control biasing concurrent LLM traffic towards fewer distinct
simultaneously-active models."""

from __future__ import annotations

import asyncio

from app.llm.scheduler import ModelAffinityScheduler


async def test_acquire_admits_up_to_max_loaded_immediately() -> None:
    sched = ModelAffinityScheduler(max_loaded=2, max_wait_s=5.0)
    await asyncio.wait_for(sched.acquire("a"), timeout=0.1)
    await asyncio.wait_for(sched.acquire("b"), timeout=0.1)
    assert sched.snapshot() == {"a": 1, "b": 1}


async def test_repeated_acquire_of_an_already_loaded_model_never_blocks() -> None:
    """A model already in the active set never counts as a *new* one --
    unbounded concurrent calls to the same model (however many turns are
    using it right now) never queue behind capacity."""
    sched = ModelAffinityScheduler(max_loaded=1, max_wait_s=5.0)
    await asyncio.wait_for(sched.acquire("a"), timeout=0.1)
    await asyncio.wait_for(sched.acquire("a"), timeout=0.1)
    await asyncio.wait_for(sched.acquire("a"), timeout=0.1)
    assert sched.snapshot() == {"a": 3}


async def test_acquire_for_a_new_model_waits_at_capacity_then_admits_on_release() -> (
    None
):
    sched = ModelAffinityScheduler(max_loaded=1, max_wait_s=5.0)
    await sched.acquire("a")

    task = asyncio.create_task(sched.acquire("b"))
    await asyncio.sleep(0.05)
    assert not task.done(), "b should be waiting -- a already holds the one slot"

    await sched.release("a")
    await asyncio.wait_for(task, timeout=1.0)
    assert sched.snapshot() == {"b": 1}


async def test_acquire_force_admits_after_max_wait_timeout() -> None:
    """Never starves a turn forever: if the active set stays full past
    max_wait_s, admit anyway rather than block indefinitely."""
    sched = ModelAffinityScheduler(max_loaded=1, max_wait_s=0.05)
    await sched.acquire("a")  # never released during this test

    start = asyncio.get_event_loop().time()
    await asyncio.wait_for(sched.acquire("b"), timeout=1.0)
    elapsed = asyncio.get_event_loop().time() - start

    assert elapsed >= 0.05
    assert sched.snapshot() == {"a": 1, "b": 1}


async def test_release_drops_a_model_from_the_active_set_at_zero_in_flight() -> None:
    sched = ModelAffinityScheduler(max_loaded=2, max_wait_s=5.0)
    await sched.acquire("a")
    await sched.acquire("a")
    assert sched.snapshot() == {"a": 2}
    await sched.release("a")
    assert sched.snapshot() == {"a": 1}
    await sched.release("a")
    assert sched.snapshot() == {}


async def test_three_models_at_max_loaded_two_forces_a_wait_not_a_deadlock() -> None:
    """The scenario DECISIONS.md §13 actually describes: three concurrent
    tables wanting three different models against a backend that can only
    hold two loaded. The third waits (biasing traffic towards the
    already-active two) rather than all three firing simultaneously."""
    sched = ModelAffinityScheduler(max_loaded=2, max_wait_s=5.0)
    await sched.acquire("a")
    await sched.acquire("b")

    task_c = asyncio.create_task(sched.acquire("c"))
    await asyncio.sleep(0.05)
    assert not task_c.done()
    assert sched.snapshot() == {"a": 1, "b": 1}

    await sched.release("b")
    await asyncio.wait_for(task_c, timeout=1.0)
    assert sched.snapshot() == {"a": 1, "c": 1}
