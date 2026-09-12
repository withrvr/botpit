"""Fixtures for botpit unit tests: no database, no network, no Ollama.

Tests marked ``redis`` (Lua scripts, which fakeredis lacks) use the real
``settings.REDIS_URL`` and are skipped as a whole when it is unreachable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import redis as sync_redis
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.config import settings as _settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def settings() -> Settings:
    return _settings.model_copy(
        update={
            "EVENT_BUS": "memory",
            "LLM_HEALTH_REQUIRED_AT_STARTUP": False,
            "LLM_BASE_URL": "http://127.0.0.1:9",
        }
    )


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
def scope() -> str:
    """Unique key namespace per test; ``rds`` deletes ``*{scope}*`` on teardown."""
    return f"t:{uuid4().hex}"


@pytest.fixture
async def rds(scope: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(_settings.REDIS_URL, decode_responses=True)
    yield client
    async for key in client.scan_iter(match=f"*{scope}*"):
        await client.delete(key)
    await client.aclose()
