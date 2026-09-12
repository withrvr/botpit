from __future__ import annotations

from typing import TYPE_CHECKING

import fakeredis.aioredis
import pytest

from botpit.events import EventBus, InMemoryEventBus, RedisStreamsEventBus, create_event_bus
from tests.unit import bus_contract as c

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from botpit.config import Settings

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


def test_factory_picks_by_settings(settings: Settings) -> None:
    assert isinstance(create_event_bus(settings), InMemoryEventBus)
    redis_settings = settings.model_copy(update={"event_bus": "redis"})
    assert isinstance(create_event_bus(redis_settings), RedisStreamsEventBus)
