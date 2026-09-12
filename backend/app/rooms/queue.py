"""FIFO join queue with timed seat offers on Redis (docs/capacity-and-queueing.md).

Keys per ``scope`` (``"global"`` for the concurrent-game cap, ``"room:{id}"``
for seats):

- ``queue:{scope}``        ZSET user -> enqueue time ms (``ZADD NX``: idempotent join)
- ``offers:{scope}``       ZSET user -> offer deadline ms (what ``sweep`` scans)
- ``offer:{scope}:{user}`` seat number, ``SET NX PX`` so Redis expires it too
- ``scores:{scope}``       HASH user -> original enqueue score, restored on requeue
- ``misses:{scope}:{user}``  expired offers so far; dropped at ``max_misses``
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from app.telemetry import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = get_logger("queue")

# KEYS = queue, offers, scores; ARGV = now_ms, ttl_ms, seat, offer key prefix.
# Returns {user, deadline_ms} or nil when the queue is empty.
_OFFER_NEXT = """
local popped = redis.call('ZPOPMIN', KEYS[1])
if #popped == 0 then return nil end
local user = popped[1]
local deadline = tonumber(ARGV[1]) + tonumber(ARGV[2])
redis.call('SET', ARGV[4] .. user, ARGV[3], 'NX', 'PX', ARGV[2])
redis.call('ZADD', KEYS[2], deadline, user)
redis.call('HSET', KEYS[3], user, popped[2])
return {user, deadline}
"""


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class QueueStatus(BaseModel):
    position: int | None
    length: int
    eta_seconds: int | None


class JoinQueue:
    def __init__(
        self, redis: Redis, *, offer_ttl_s: int = 30, max_misses: int = 2
    ) -> None:
        self._redis = redis
        self.offer_ttl_s = offer_ttl_s
        self.max_misses = max_misses
        self._offer_next = redis.register_script(_OFFER_NEXT)

    async def enqueue(self, scope: str, user_id: str) -> int:
        """Join (or re-join, no-op) the queue; returns the 1-based position."""
        added = await self._redis.zadd(f"queue:{scope}", {user_id: _now_ms()}, nx=True)
        position = await self.position(scope, user_id)
        assert position is not None
        if added:
            log.info(
                "queue enqueue key=queue:%s user=%s position=%s",
                scope,
                user_id,
                position,
            )
        return position

    async def position(self, scope: str, user_id: str) -> int | None:
        rank = cast("int | None", await self._redis.zrank(f"queue:{scope}", user_id))
        return None if rank is None else rank + 1

    async def length(self, scope: str) -> int:
        return await self._redis.zcard(f"queue:{scope}")

    async def leave(self, scope: str, user_id: str) -> None:
        """Remove the user from the queue and drop any pending offer."""
        await self._redis.zrem(f"queue:{scope}", user_id)
        await self._forget(scope, user_id)
        log.info("queue leave key=queue:%s user=%s", scope, user_id)

    async def offer_next(self, scope: str, seat: int) -> tuple[str, int] | None:
        """Pop the head of the queue and hold ``seat`` for them; ``(user, deadline_ms)``."""
        result = await self._offer_next(
            keys=[f"queue:{scope}", f"offers:{scope}", f"scores:{scope}"],
            args=[_now_ms(), self.offer_ttl_s * 1000, seat, f"offer:{scope}:"],
        )
        if result is None:
            return None
        user_id, deadline = str(result[0]), int(result[1])
        log.info(
            "queue offer key=offer:%s:%s seat=%s deadline=%s",
            scope,
            user_id,
            seat,
            deadline,
        )
        return user_id, deadline

    async def accept(self, scope: str, user_id: str) -> int | None:
        """Claim the offered seat; ``None`` if there is no live offer."""
        seat = await self._redis.getdel(f"offer:{scope}:{user_id}")
        await self._forget(scope, user_id)
        if seat is None:
            return None
        log.info("queue accept key=offer:%s:%s seat=%s", scope, user_id, seat)
        return int(seat)

    async def sweep(self, scope: str, now_ms: int | None = None) -> list[str]:
        """Requeue users whose offer expired (at their original score); drop repeat no-shows."""
        now = _now_ms() if now_ms is None else now_ms
        offers = f"offers:{scope}"
        requeued: list[str] = []
        expired = cast(
            "list[str]", await self._redis.zrangebyscore(offers, "-inf", now)
        )
        for user_id in expired:
            if not await self._redis.zrem(offers, user_id):
                continue  # accepted (or swept by another worker) meanwhile
            await self._redis.delete(f"offer:{scope}:{user_id}")
            score = await self._redis.hget(f"scores:{scope}", user_id)
            misses_key = f"misses:{scope}:{user_id}"
            misses = int(await self._redis.incrby(misses_key, 1))
            await self._redis.expire(misses_key, 3600)
            if misses >= self.max_misses:
                await self._forget(scope, user_id)
                log.info(
                    "queue drop key=queue:%s user=%s misses=%s", scope, user_id, misses
                )
                continue
            await self._redis.zadd(
                f"queue:{scope}", {user_id: float(score) if score else now}, nx=True
            )
            requeued.append(user_id)
            log.info(
                "queue requeue key=queue:%s user=%s misses=%s", scope, user_id, misses
            )
        return requeued

    @staticmethod
    def eta_seconds(
        position: int,
        oldest_started_at: datetime | None,
        typical_game_s: int = 900,
    ) -> int | None:
        """Pessimistic single-lane guess: the oldest game finishes first, then one per slot."""
        if oldest_started_at is None:
            return None
        elapsed = (datetime.now(UTC) - oldest_started_at).total_seconds()
        remaining = max(0, typical_game_s - int(elapsed))
        return remaining + (position - 1) * typical_game_s

    async def snapshot(
        self, scope: str, user_id: str, oldest_started_at: datetime | None = None
    ) -> QueueStatus:
        position = await self.position(scope, user_id)
        return QueueStatus(
            position=position,
            length=await self.length(scope),
            eta_seconds=None
            if position is None
            else self.eta_seconds(position, oldest_started_at),
        )

    async def _forget(self, scope: str, user_id: str) -> None:
        await self._redis.zrem(f"offers:{scope}", user_id)
        await self._redis.delete(
            f"offer:{scope}:{user_id}", f"misses:{scope}:{user_id}"
        )
        await self._redis.hdel(f"scores:{scope}", user_id)
