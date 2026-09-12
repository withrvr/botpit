from __future__ import annotations

from typing import TYPE_CHECKING

import fakeredis.aioredis
import pytest

from app.events import (
    EventBus,
    InMemoryEventBus,
    RedisStreamsEventBus,
    create_event_bus,
)
from tests.unit import bus_contract as c

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.core.config import Settings

MAXLEN = 3


@pytest.fixture(params=["memory", "redis"])
async def bus(request: pytest.FixtureRequest) -> AsyncIterator[EventBus]:
    bus: EventBus
    if request.param == "memory":
        bus = InMemoryEventBus(maxlen=MAXLEN)
    else:
        bus = RedisStreamsEventBus(fakeredis.aioredis.FakeRedis(), maxlen=MAXLEN)
    yield bus
    await bus.close()


async def test_publish_assigns_seq_per_room(bus: EventBus) -> None:
    await c.publish_assigns_seq_per_room(bus)


async def test_subscribe_live_receives_new_events(bus: EventBus) -> None:
    await c.subscribe_live_receives_new_events(bus)


async def test_subscribe_since_zero_replays_then_live(bus: EventBus) -> None:
    await c.subscribe_since_zero_replays_then_live(bus)


async def test_subscribe_since_id_resumes_after_it(bus: EventBus) -> None:
    await c.subscribe_since_id_resumes_after_it(bus)


async def test_two_subscribers_both_get_every_event(bus: EventBus) -> None:
    await c.two_subscribers_both_get_every_event(bus)


async def test_maxlen_keeps_newest(bus: EventBus) -> None:
    await c.maxlen_keeps_newest(bus, MAXLEN)


async def test_history_limit(bus: EventBus) -> None:
    await c.history_limit(bus)


async def test_close_stops_subscribers(bus: EventBus) -> None:
    await c.close_stops_subscribers(bus)


async def test_memory_maxlen_is_exact() -> None:
    bus = InMemoryEventBus(maxlen=2)
    await c.publish_n(bus, "r", 5)
    assert [e.seq for e in await bus.history("r")] == [4, 5]


async def test_factory_picks_by_settings(settings: Settings) -> None:
    memory = create_event_bus(settings)
    redis_bus = create_event_bus(settings.model_copy(update={"EVENT_BUS": "redis"}))
    try:
        assert isinstance(memory, InMemoryEventBus)
        assert isinstance(redis_bus, RedisStreamsEventBus)
    finally:
        await memory.close()
        await redis_bus.close()


async def test_concurrent_publishers_keep_order() -> None:
    for bus in (
        InMemoryEventBus(maxlen=1000),
        RedisStreamsEventBus(fakeredis.aioredis.FakeRedis(), maxlen=1000),
    ):
        await c.concurrent_publishers_keep_seq_and_stream_order(bus, 1000)
        await bus.close()


async def test_ping(bus: EventBus) -> None:
    await c.ping_reflects_open_state(bus)
