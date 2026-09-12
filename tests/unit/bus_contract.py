"""Behavioural contract every EventBus implementation must satisfy.

Plain coroutines taking a bus so unit (in-memory, fakeredis) and integration
(real Redis) suites share one body.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest

from botpit.events import PUBLIC, ChatMessage, EventEnvelope, make_event

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from botpit.events import EventBus

TIMEOUT = 2.0


def room() -> str:
    return uuid.uuid4().hex


def event(room_id: str, n: int) -> EventEnvelope:
    return make_event(room_id, ChatMessage(seat=0, text=str(n)), PUBLIC)


async def publish_n(bus: EventBus, room_id: str, n: int) -> list[EventEnvelope]:
    return [await bus.publish(event(room_id, i)) for i in range(1, n + 1)]


async def nxt(it: AsyncIterator[EventEnvelope]) -> EventEnvelope:
    return await asyncio.wait_for(anext(it), TIMEOUT)


async def take(it: AsyncIterator[EventEnvelope], n: int) -> list[EventEnvelope]:
    return [await nxt(it) for _ in range(n)]


async def publish_assigns_seq_per_room(bus: EventBus) -> None:
    a, b = room(), room()
    got_a = await publish_n(bus, a, 3)
    got_b = await publish_n(bus, b, 2)
    assert [e.seq for e in got_a] == [1, 2, 3]
    assert [e.seq for e in got_b] == [1, 2]
    assert all(e.stream_id for e in got_a + got_b)
    assert all(e.room_id == a for e in got_a)


async def subscribe_live_receives_new_events(bus: EventBus) -> None:
    r = room()
    await publish_n(bus, r, 2)
    it = bus.subscribe(r)
    # Prime the generator so it has taken its "now" position before we publish.
    pending = asyncio.ensure_future(nxt(it))
    await asyncio.sleep(0.05)
    await publish_n(bus, r, 1)
    first = await pending
    assert first.seq == 3
    await it.aclose()


async def subscribe_since_zero_replays_then_live(bus: EventBus) -> None:
    r = room()
    await publish_n(bus, r, 3)
    it = bus.subscribe(r, since="0")
    assert [e.seq for e in await take(it, 3)] == [1, 2, 3]
    await bus.publish(event(r, 4))
    assert (await nxt(it)).seq == 4
    await it.aclose()


async def subscribe_since_id_resumes_after_it(bus: EventBus) -> None:
    r = room()
    published = await publish_n(bus, r, 5)
    second = published[1].stream_id
    assert second is not None
    it = bus.subscribe(r, since=second)
    assert [e.seq for e in await take(it, 3)] == [3, 4, 5]
    await bus.publish(event(r, 6))
    assert (await nxt(it)).seq == 6
    await it.aclose()


async def two_subscribers_both_get_every_event(bus: EventBus) -> None:
    r = room()
    it1, it2 = bus.subscribe(r, since="0"), bus.subscribe(r, since="0")
    await publish_n(bus, r, 3)
    assert [e.seq for e in await take(it1, 3)] == [1, 2, 3]
    assert [e.seq for e in await take(it2, 3)] == [1, 2, 3]
    await it1.aclose()
    await it2.aclose()


async def maxlen_keeps_newest(bus: EventBus, maxlen: int) -> None:
    r = room()
    n = maxlen + 2
    await publish_n(bus, r, n)
    seqs = [e.seq for e in await bus.history(r)]
    assert len(seqs) <= n
    assert seqs[-maxlen:] == list(range(n - maxlen + 1, n + 1))


async def history_limit(bus: EventBus) -> None:
    r = room()
    published = await publish_n(bus, r, 3)
    assert [e.seq for e in await bus.history(r, limit=2)] == [1, 2]
    assert [e.seq for e in await bus.history(r, since=published[0].stream_id)] == [2, 3]
    assert [e.seq for e in await bus.history(r, since="0", limit=10)] == [1, 2, 3]


async def close_stops_subscribers(bus: EventBus) -> None:
    r = room()
    it = bus.subscribe(r)
    pending = asyncio.ensure_future(nxt(it))
    await asyncio.sleep(0.05)
    await bus.close()
    with pytest.raises(StopAsyncIteration):
        await pending


async def concurrent_publishers_keep_seq_and_stream_order(bus: EventBus, maxlen: int) -> None:
    """Many tasks publishing to one room: seq is 1..n with no gaps and matches stream order."""
    room_id = room()
    n = min(maxlen, 40)
    results = await asyncio.gather(*(bus.publish(event(room_id, i)) for i in range(n)))
    assert sorted(e.seq for e in results if e.seq is not None) == list(range(1, n + 1))
    history = await bus.history(room_id, since="0", limit=n)
    assert [e.seq for e in history] == list(range(1, n + 1))


async def ping_reflects_open_state(bus: EventBus) -> None:
    assert await bus.ping() is True
    await bus.close()
    assert await bus.ping() is False
