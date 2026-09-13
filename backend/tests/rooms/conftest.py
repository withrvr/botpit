"""Fixtures for RoomService/API tests.

``tests/conftest.py`` (the `client`/`db`/`*_token_headers` fixtures) is an
ancestor of this directory and applies automatically. Redis is not: unlike
``tests/unit/conftest.py`` (not an ancestor of ``tests/rooms/``), so the
``redis``-marker skip and the raw async client are re-declared here, mirroring
that module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import redis as sync_redis
from redis.asyncio import Redis
from sqlmodel import Session

from app.core.config import settings as _settings
from app.events import InMemoryEventBus
from app.llm import ModelConfig, ModelRegistry
from app.referee import GameRunner
from app.rooms.queue import JoinQueue
from app.rooms.service import RoomService


def _redis_reachable() -> bool:
    try:
        return bool(
            sync_redis.Redis.from_url(
                _settings.REDIS_URL, socket_connect_timeout=1
            ).ping()
        )
    except sync_redis.RedisError:
        return False


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if (
        any(item.get_closest_marker("redis") for item in items)
        and not _redis_reachable()
    ):
        skip = pytest.mark.skip(reason=f"Redis unreachable at {_settings.REDIS_URL}")
        for item in items:
            if item.get_closest_marker("redis"):
                item.add_marker(skip)


@pytest.fixture
async def rds() -> AsyncIterator[Redis]:
    client = Redis.from_url(_settings.REDIS_URL, decode_responses=True)
    yield client
    for prefix in ("queue:", "offers:", "offer:", "scores:", "misses:"):
        async for key in client.scan_iter(match=f"{prefix}*"):
            await client.delete(key)
    await client.aclose()


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus(maxlen=10_000)


@pytest.fixture
def runner(bus: InMemoryEventBus) -> GameRunner:
    return GameRunner(bus)


@pytest.fixture
def registry() -> ModelRegistry:
    """Points at a port nothing listens on: an ``AgentController`` call fails
    fast (connection refused) instead of depending on a real, reachable LLM
    backend -- these tests only need to prove the wiring, not play a full
    game (that needs a real Ollama, exercised separately by ``tests/live``)."""
    cfg = ModelConfig(
        model="test-model",
        provider="ollama_native",
        base_url="http://127.0.0.1:9",
        num_ctx=1024,
        keep_alive="1m",
        timeout_s=2.0,
    )
    return ModelRegistry([cfg], default_model="test-model")


@pytest.fixture
async def queue(rds: Redis) -> JoinQueue:
    return JoinQueue(rds)


@pytest.fixture
async def service(
    db: Session,
    runner: GameRunner,
    queue: JoinQueue,
    registry: ModelRegistry,
    bus: InMemoryEventBus,
) -> RoomService:
    return RoomService(db, runner, queue, registry, bus)
