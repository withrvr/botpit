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
#
# The offer string key's own PX gets 4x headroom over the ZSET deadline --
# sweep() is what's supposed to reclaim an expired offer (ZRANGEBYSCORE then
# GETDEL), and it must always find the key still there to GETDEL. Giving the
# key the *same* TTL as the ZSET deadline made Redis's own automatic expiry
# race sweep()'s explicit reclaim for the same millisecond -- when Redis won,
# GETDEL found nothing and sweep() wrongly assumed some other worker already
# claimed it, silently dropping the seat instead of reclaiming it. The PX
# here is just a leak backstop (a crashed process's key still eventually
# vanishes); sweep() is the real reclaim path and must win the race.
_OFFER_NEXT = """
local popped = redis.call('ZPOPMIN', KEYS[1])
if #popped == 0 then return nil end
local user = popped[1]
local ttl = tonumber(ARGV[2])
local deadline = tonumber(ARGV[1]) + ttl
redis.call('SET', ARGV[4] .. user, ARGV[3], 'NX', 'PX', ttl * 4)
redis.call('ZADD', KEYS[2], deadline, user)
redis.call('HSET', KEYS[3], user, popped[2])
return {user, deadline}
"""

# KEYS = offer string key, offers ZSET; ARGV = user. Atomically takes the
# offer: whichever of accept() and sweep() runs this first for a given user
# wins (GETDEL + ZREM in one script), the other gets nil and does nothing.
# This is what stops a user who accepted right at the deadline from also
# being swept-and-requeued by a racing sweep() call, and vice versa.
_CLAIM_OFFER = """
local seat = redis.call('GETDEL', KEYS[1])
if seat then
    redis.call('ZREM', KEYS[2], ARGV[1])
end
return seat
"""


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class QueueStatus(BaseModel):
    position: int | None
    length: int
    eta_seconds: int | None
    offered: bool = False
    offer_expires_in_seconds: int | None = None


class JoinQueue:
    def __init__(
        self, redis: Redis, *, offer_ttl_s: int = 30, max_misses: int = 2
    ) -> None:
        self._redis = redis
        self.offer_ttl_s = offer_ttl_s
        self.max_misses = max_misses
        self._offer_next = redis.register_script(_OFFER_NEXT)
        self._claim_offer = redis.register_script(_CLAIM_OFFER)

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
        """Claim the offered seat; ``None`` if there is no live offer.

        Goes through ``_claim_offer``, the same atomic step ``sweep`` uses, so
        a racing sweep can never also requeue a seat this call just accepted.
        """
        seat = await self._claim_offer(
            keys=[f"offer:{scope}:{user_id}", f"offers:{scope}"], args=[user_id]
        )
        if seat is None:
            return None
        await self._forget(scope, user_id)
        log.info("queue accept key=offer:%s:%s seat=%s", scope, user_id, seat)
        return int(seat)

    async def sweep(self, scope: str, now_ms: int | None = None) -> list[int]:
        """Reclaims every seat held by an expired offer -- requeuing that
        user at their original score, or dropping them after
        ``max_misses``. Returns the *seats* reclaimed (one per expired
        offer, regardless of whether that offer's user was requeued or
        dropped): the caller's real question after a sweep is "which
        seats are free again," not who specifically missed one -- fairness
        bookkeeping for the *user* side is already fully handled here and
        needs no further action from the caller."""
        now = _now_ms() if now_ms is None else now_ms
        offers = f"offers:{scope}"
        reclaimed: list[int] = []
        expired = cast(
            "list[str]", await self._redis.zrangebyscore(offers, "-inf", now)
        )
        for user_id in expired:
            seat = await self._claim_offer(
                keys=[f"offer:{scope}:{user_id}", offers], args=[user_id]
            )
            if seat is None:
                continue  # accepted (or already swept by another worker) meanwhile
            reclaimed.append(int(seat))
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
            log.info(
                "queue requeue key=queue:%s user=%s misses=%s", scope, user_id, misses
            )
        return reclaimed

    async def pop_next(self, scope: str) -> str | None:
        """Plain FIFO pop, no offer/TTL/accept semantics -- for a scope
        where nobody needs to explicitly accept (the caller acts
        immediately on the popped id, e.g. a room auto-starting once its
        turn comes up; DECISIONS.md §2's global concurrent-game cap)."""
        popped = await self._redis.zpopmin(f"queue:{scope}")
        if not popped:
            return None
        member, _score = cast("tuple[str, float]", popped[0])
        log.info("queue pop key=queue:%s user=%s", scope, member)
        return str(member)

    async def peek_head(self, scope: str) -> str | None:
        """Read the head of the queue without removing it."""
        head = cast("list[str]", await self._redis.zrange(f"queue:{scope}", 0, 0))
        return head[0] if head else None

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
        offered = False
        offer_expires_in_seconds = None
        if position is None:
            # Once offered, a user is popped out of `queue:{scope}` (so
            # `position` above is already None) into a live offer -- the
            # caller (a polling frontend) still needs to know "your turn
            # came up, go accept" rather than reading plain silence as
            # "still waiting" or "gave up." Both reads are non-destructive,
            # unlike `accept()`/`sweep()`'s GETDEL. The deadline comes from
            # the `offers` ZSET, not the offer key's own PTTL -- that key's
            # PX is deliberately 4x the real deadline (see _OFFER_NEXT), so
            # its PTTL alone would overstate how long the offer is actually
            # good for.
            deadline = await self._redis.zscore(f"offers:{scope}", user_id)
            if deadline is not None:
                offered = True
                offer_expires_in_seconds = max(0, int((deadline - _now_ms()) / 1000))
        return QueueStatus(
            position=position,
            length=await self.length(scope),
            eta_seconds=None
            if position is None
            else self.eta_seconds(position, oldest_started_at),
            offered=offered,
            offer_expires_in_seconds=offer_expires_in_seconds,
        )

    async def _forget(self, scope: str, user_id: str) -> None:
        await self._redis.zrem(f"offers:{scope}", user_id)
        await self._redis.delete(
            f"offer:{scope}:{user_id}", f"misses:{scope}:{user_id}"
        )
        await self._redis.hdel(f"scores:{scope}", user_id)
