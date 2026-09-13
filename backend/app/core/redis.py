"""One shared ``redis.asyncio`` client for request-scoped features (rate limits, queues).

The event bus keeps its own connection (``app.events.redis_bus``): it blocks on
``XREAD`` and must not share a pool with short request/response commands.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from app.core.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


RedisDep = Annotated[Redis, Depends(get_redis)]
