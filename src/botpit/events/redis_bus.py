"""Redis Streams bus for multi-worker deployments.

One stream per room, ``XADD``/``XREAD``/``XRANGE`` only. No consumer groups:
every subscriber wants every event, and Streams (unlike Pub/Sub) let a
reconnecting client resume from its last ``stream_id``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from botpit.events.schema import EventEnvelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _decode(entry_id: Any, fields: dict[Any, Any]) -> EventEnvelope:
    raw = fields.get("e", fields.get(b"e"))
    if raw is None:
        msg = f"stream entry {_str(entry_id)} has no 'e' field"
        raise ValueError(msg)
    return EventEnvelope.model_validate_json(raw).model_copy(update={"stream_id": _str(entry_id)})


class RedisStreamsEventBus:
    def __init__(self, redis: Redis, maxlen: int = 10_000) -> None:
        self._redis = redis
        self._maxlen = maxlen
        self._closed = asyncio.Event()

    @classmethod
    def from_url(cls, url: str, maxlen: int = 10_000) -> RedisStreamsEventBus:
        return cls(Redis.from_url(url), maxlen)

    @staticmethod
    def stream_key(room_id: str) -> str:
        return f"botpit:room:{room_id}:events"

    async def publish(self, event: EventEnvelope) -> EventEnvelope:
        if self._closed.is_set():
            msg = "event bus is closed"
            raise RuntimeError(msg)
        # ponytail: INCR then XADD is two round trips; concurrent publishers to one
        # room can interleave seq vs stream order. Rooms publish from one task today.
        seq = int(await self._redis.incr(f"botpit:room:{event.room_id}:seq"))
        published = event.model_copy(update={"seq": seq})
        entry_id = await self._redis.xadd(
            self.stream_key(event.room_id),
            {"e": published.model_dump_json()},
            maxlen=self._maxlen,
            approximate=True,
        )
        return published.model_copy(update={"stream_id": _str(entry_id)})

    async def subscribe(
        self, room_id: str, since: str | None = None
    ) -> AsyncIterator[EventEnvelope]:
        key = self.stream_key(room_id)
        # Resolve "now" to a concrete id: "$" would miss events landing between XREAD calls.
        last = since if since is not None else await self._last_id(key)
        while not self._closed.is_set():
            try:
                streams: Any = await self._redis.xread({key: last}, block=1000)
            except RedisError:
                if self._closed.is_set():
                    return
                raise
            for _, entries in streams or []:
                for entry_id, fields in entries:
                    yield _decode(entry_id, fields)
                    last = _str(entry_id)

    async def history(
        self, room_id: str, since: str | None = None, limit: int = 1000
    ) -> list[EventEnvelope]:
        start = "-" if since in (None, "0") else since
        key = self.stream_key(room_id)
        entries: Any = await self._redis.xrange(key, min=start, max="+", count=limit + 1)
        events = [_decode(i, f) for i, f in entries if _str(i) != since]
        return events[:limit]

    async def close(self) -> None:
        self._closed.set()
        await self._redis.aclose()

    async def _last_id(self, key: str) -> str:
        entries: Any = await self._redis.xrevrange(key, count=1)
        return _str(entries[0][0]) if entries else "0-0"
