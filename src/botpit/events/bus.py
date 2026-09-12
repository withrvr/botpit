"""Event bus interface, in-process implementation and factory."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from botpit.config import Settings
    from botpit.events.schema import EventEnvelope


class EventBus(Protocol):
    async def publish(self, event: EventEnvelope) -> EventEnvelope:
        """Assign ``seq`` and ``stream_id``, append to the room stream, return the result."""
        ...

    def subscribe(self, room_id: str, since: str | None = None) -> AsyncIterator[EventEnvelope]:
        """``since=None``: live only. ``"0"``: replay all. ``<stream_id>``: everything after it."""
        ...

    async def history(
        self, room_id: str, since: str | None = None, limit: int = 1000
    ) -> list[EventEnvelope]: ...

    async def ping(self) -> bool:
        """True when the backing store answers; used by ``/health``."""
        ...

    async def close(self) -> None: ...


def _seq_of(since: str) -> int:
    return int(since.split("-", 1)[0])


class InMemoryEventBus:
    """Single-process bus: per-room bounded deque, one condition for wakeups."""

    def __init__(self, maxlen: int = 10_000) -> None:
        self._rooms: dict[str, deque[EventEnvelope]] = defaultdict(lambda: deque(maxlen=maxlen))
        self._seq: dict[str, int] = defaultdict(int)
        # ponytail: one condition wakes every subscriber on any publish; per-room if it shows up.
        self._cond = asyncio.Condition()
        self._closed = False

    async def publish(self, event: EventEnvelope) -> EventEnvelope:
        if self._closed:
            msg = "event bus is closed"
            raise RuntimeError(msg)
        seq = self._seq[event.room_id] + 1
        self._seq[event.room_id] = seq
        published = event.model_copy(update={"seq": seq, "stream_id": f"{seq}-0"})
        self._rooms[event.room_id].append(published)
        async with self._cond:
            self._cond.notify_all()
        return published

    async def subscribe(
        self, room_id: str, since: str | None = None
    ) -> AsyncIterator[EventEnvelope]:
        cursor = self._seq[room_id] if since is None else _seq_of(since)
        while True:
            for event in self._after(room_id, cursor):
                yield event
                cursor = event.seq or cursor
            if self._closed:
                return
            async with self._cond:
                while not (self._closed or self._seq[room_id] > cursor):
                    await self._cond.wait()

    async def history(
        self, room_id: str, since: str | None = None, limit: int = 1000
    ) -> list[EventEnvelope]:
        return self._after(room_id, 0 if since is None else _seq_of(since))[:limit]

    async def ping(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        self._closed = True
        async with self._cond:
            self._cond.notify_all()

    # ponytail: linear scan of the room deque per wake; index by seq if maxlen grows large.
    def _after(self, room_id: str, cursor: int) -> list[EventEnvelope]:
        return [e for e in self._rooms[room_id] if (e.seq or 0) > cursor]


def create_event_bus(settings: Settings) -> EventBus:
    if settings.event_bus == "redis":
        from botpit.events.redis_bus import RedisStreamsEventBus

        return RedisStreamsEventBus.from_url(settings.redis_url, settings.event_stream_maxlen)
    return InMemoryEventBus(settings.event_stream_maxlen)
