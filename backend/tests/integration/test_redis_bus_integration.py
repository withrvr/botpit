"""Same bus contract against a real Redis. Set REDIS_URL to enable."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import redis

from app.events import EventBus, RedisStreamsEventBus
from tests.unit import bus_contract as c

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL")


def _reachable(url: str | None) -> bool:
    if not url:
        return False
    try:
        return bool(redis.Redis.from_url(url, socket_connect_timeout=1).ping())
    except redis.RedisError:
        return False


if not _reachable(REDIS_URL):
    pytest.skip("REDIS_URL not set or Redis unreachable", allow_module_level=True)

MAXLEN = 3


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    assert REDIS_URL is not None
    bus = RedisStreamsEventBus.from_url(REDIS_URL, maxlen=MAXLEN)
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


async def test_concurrent_publishers_keep_order() -> None:
    bus = RedisStreamsEventBus.from_url(REDIS_URL, maxlen=1000)
    try:
        await c.concurrent_publishers_keep_seq_and_stream_order(bus, 1000)
    finally:
        await bus.close()
