"""Model-affinity admission control (Phase 16, DECISIONS.md §13): "the turn
scheduler treats model identity as a resource: group consecutive AI turns
by model where turn order allows, prefer tables that share a model, and
surface swap counts in metrics."

One instance per LLM base_url (a completely separate backend/VRAM budget
from any other base_url). Loading a model costs real seconds
(docs/local-model-setup.md §2.3); keep-alive tuning only delays an
eviction, it never removes the swap cost. This scheduler doesn't decide
turn *order* within one game -- that's the referee's job, fixed by each
game's own rules -- it decides *when a turn's already-due LLM call is
allowed to actually fire*, across every room sharing one backend, so a
request for a model with calls already in flight gets serviced ahead of
one that would grow the active set past capacity, wherever the referee
level leaves that choice open. That's what "prefer tables that share a
model" means at the layer this code can actually act on.

Bounded, not a hard priority queue: a request for a model outside the
active set waits at most ``max_wait_s`` for room to open before being
admitted anyway. A turn's own deadline (``SeatSpec.timeout_s``) must never
be blocked forever by scheduling -- a liveness guarantee, not just an
optimization.

Heuristic, not a perfect model of Ollama's own VRAM state: this tracks
"models with at least one in-flight call," not Ollama's actual keep-alive
residency (a model can still be resident with zero in-flight calls, for
up to ``LLM_KEEP_ALIVE``). Querying Ollama's own `/api/ps` for ground
truth would be more accurate but costs a network round trip per
admission decision; not built here -- this is a bias towards fewer swaps
under real concurrent load, not a guarantee.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from app.telemetry import get_logger

log = get_logger("llm.scheduler")


class ModelAffinityScheduler:
    def __init__(self, max_loaded: int, *, max_wait_s: float = 5.0) -> None:
        self._max_loaded = max(1, max_loaded)
        self._max_wait_s = max_wait_s
        self._in_flight: dict[str, int] = {}
        self._waiters: dict[str, list[asyncio.Event]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def acquire(self, model: str) -> None:
        """Blocks (briefly, boundedly) until ``model`` may make its call."""
        loop = asyncio.get_running_loop()
        deadline: float | None = None
        while True:
            async with self._lock:
                if model in self._in_flight or len(self._in_flight) < self._max_loaded:
                    self._in_flight[model] = self._in_flight.get(model, 0) + 1
                    return
                event = asyncio.Event()
                self._waiters[model].append(event)

            if deadline is None:
                deadline = loop.time() + self._max_wait_s
            remaining = deadline - loop.time()
            if remaining <= 0:
                async with self._lock:
                    self._forget_waiter(model, event)
                    self._force_admit(model)
                return

            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except TimeoutError:
                async with self._lock:
                    self._forget_waiter(model, event)
                    self._force_admit(model)
                return
            # Woken because a slot actually freed (release() only wakes on
            # that) -- loop back and re-check under the lock rather than
            # blindly admitting: another waiter for a *different* model
            # could already have taken it in the meantime.
            async with self._lock:
                self._forget_waiter(model, event)

    def _forget_waiter(self, model: str, event: asyncio.Event) -> None:
        waiters = self._waiters.get(model)
        if waiters and event in waiters:
            waiters.remove(event)

    def _force_admit(self, model: str) -> None:
        self._in_flight[model] = self._in_flight.get(model, 0) + 1
        log.info(
            "model affinity scheduler: forced admit for %r after "
            "%.1fs wait -- the active model set stayed full",
            model,
            self._max_wait_s,
        )

    async def release(self, model: str) -> None:
        async with self._lock:
            count = self._in_flight.get(model, 0) - 1
            if count > 0:
                self._in_flight[model] = count
                return
            # The model dropped out of the active set entirely -- a slot is
            # now actually free, unlike a call to a model that still has
            # other in-flight calls (count > 0 above): waking a waiter then
            # would let it self-admit into a set that never shrank, pushing
            # the active set past max_loaded. Wake exactly one waiter (of
            # any model waiting), not every one, to avoid a thundering herd
            # all re-checking for the one slot that opened.
            self._in_flight.pop(model, None)
            for waiters in self._waiters.values():
                if waiters:
                    waiters.pop(0).set()
                    break

    def snapshot(self) -> dict[str, int]:
        """Read-only view of in-flight counts per model, for tests/introspection."""
        return dict(self._in_flight)
