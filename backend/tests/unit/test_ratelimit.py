from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api.deps import get_current_user
from app.core.redis import get_redis
from app.ratelimit import RateLimited, check_rate, rate_limit, rate_limited_handler

if TYPE_CHECKING:
    from redis.asyncio import Redis

pytestmark = pytest.mark.redis

T0 = 1_700_000_000_000


async def test_allows_limit_then_denies_with_retry_after(
    rds: Redis, scope: str
) -> None:
    key = f"rl:{scope}:u1"
    for _ in range(3):
        assert await check_rate(rds, key, 3, 60, now_ms=T0) == (True, 0)
    assert await check_rate(rds, key, 3, 60, now_ms=T0) == (False, 60)
    assert await check_rate(rds, key, 3, 60, now_ms=T0 + 59_500) == (False, 1)


async def test_window_slides(rds: Redis, scope: str) -> None:
    key = f"rl:{scope}:u1"
    assert await check_rate(rds, key, 1, 10, now_ms=T0) == (True, 0)
    assert await check_rate(rds, key, 1, 10, now_ms=T0 + 5_000) == (False, 5)
    assert await check_rate(rds, key, 1, 10, now_ms=T0 + 10_000) == (True, 0)
    assert await check_rate(rds, key, 1, 10, now_ms=T0 + 10_001) == (False, 10)
    assert int(await rds.pttl(key)) > 0


async def test_keys_are_independent(rds: Redis, scope: str) -> None:
    assert await check_rate(rds, f"rl:{scope}:a", 1, 60, now_ms=T0) == (True, 0)
    assert await check_rate(rds, f"rl:{scope}:b", 1, 60, now_ms=T0) == (True, 0)
    assert await check_rate(rds, f"rl:{scope}:a", 1, 60, now_ms=T0) == (False, 60)


async def test_dependency_returns_429(rds: Redis, scope: str) -> None:
    app = FastAPI()
    app.add_exception_handler(RateLimited, rate_limited_handler)
    user = SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_redis] = lambda: rds

    @app.get("/ping", dependencies=[Depends(rate_limit(scope, 2, 60))])
    def ping() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        for _ in range(2):
            assert (await client.get("/ping")).status_code == 200
        r = await client.get("/ping")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert r.json() == {"detail": "rate limited", "retry_after": 60}
