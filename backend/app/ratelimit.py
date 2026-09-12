"""Per-account sliding-window rate limiting on Redis (docs/capacity-and-queueing.md).

One Lua script does the whole check atomically per key, so N workers cannot
each admit "the last" request. HTTP routes add ``Depends(rate_limit(...))``;
the websocket chat handler calls :func:`check_rate_ws` directly and answers
``{"type": "error", "code": "rate_limited", "retry_after": n}`` instead of 429.
"""

from __future__ import annotations

import math
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser
from app.core.redis import RedisDep
from app.telemetry import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = get_logger("ratelimit")

# KEYS[1] = rl:{scope}:{user}; ARGV = now_ms, limit, window_ms, member nonce.
# Returns {allowed(0|1), retry_after_seconds}.
_SLIDING_WINDOW = """
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) < limit then
  redis.call('ZADD', KEYS[1], now, ARGV[4])
  redis.call('PEXPIRE', KEYS[1], window)
  return {1, 0}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
return {0, tonumber(oldest[2]) + window - now}
"""


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


async def check_rate(
    redis: Redis,
    key: str,
    limit: int,
    window_s: int,
    now_ms: int | None = None,
) -> tuple[bool, int]:
    """Record one hit on ``key``; return ``(allowed, retry_after_seconds)``."""
    now = _now_ms() if now_ms is None else now_ms
    script = redis.register_script(_SLIDING_WINDOW)
    allowed, retry_ms = await script(
        keys=[key], args=[now, limit, window_s * 1000, secrets.token_hex(8)]
    )
    return bool(allowed), math.ceil(int(retry_ms) / 1000)


#: Same check for the websocket chat handler (no HTTP dependency wiring there).
check_rate_ws = check_rate


class RateLimited(HTTPException):
    def __init__(self, key: str, retry_after: int) -> None:
        super().__init__(
            status_code=429,
            detail="rate limited",
            headers={"Retry-After": str(retry_after)},
        )
        self.key = key
        self.retry_after = retry_after


async def rate_limited_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimited)
    return JSONResponse(
        {"detail": exc.detail, "retry_after": exc.retry_after},
        status_code=exc.status_code,
        headers=exc.headers,
    )


def rate_limit(scope: str, limit: int, window_s: int) -> Callable[..., Awaitable[None]]:
    """FastAPI dependency: at most ``limit`` calls per ``window_s`` per user for ``scope``."""

    async def dependency(user: CurrentUser, redis: RedisDep) -> None:
        key = f"rl:{scope}:{user.id}"
        allowed, retry_after = await check_rate(redis, key, limit, window_s)
        if not allowed:
            log.info("rate limited key=%s retry_after=%s", key, retry_after)
            raise RateLimited(key, retry_after)

    return dependency


def rate_limit_from_setting(
    scope: str, value: tuple[int, int]
) -> Callable[..., Awaitable[None]]:
    """``rate_limit`` from a parsed ``RATE_LIMIT_*`` setting ``(count, seconds)``."""
    return rate_limit(scope, *value)
