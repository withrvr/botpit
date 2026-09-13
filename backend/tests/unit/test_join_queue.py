from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from app.rooms.queue import JoinQueue, QueueStatus

if TYPE_CHECKING:
    from redis.asyncio import Redis

pytestmark = pytest.mark.redis


@pytest.fixture
def q(rds: Redis) -> JoinQueue:
    return JoinQueue(rds, offer_ttl_s=30, max_misses=2)


async def test_fifo_positions_and_idempotent_enqueue(q: JoinQueue, scope: str) -> None:
    assert await q.enqueue(scope, "a") == 1
    assert await q.enqueue(scope, "b") == 2
    assert await q.enqueue(scope, "a") == 1
    assert await q.length(scope) == 2
    assert await q.position(scope, "zz") is None


async def test_leave_from_middle_keeps_order(q: JoinQueue, scope: str) -> None:
    for u in "abc":
        await q.enqueue(scope, u)
    await q.leave(scope, "b")
    assert await q.position(scope, "a") == 1
    assert await q.position(scope, "c") == 2
    assert await q.length(scope) == 2


async def test_offer_and_accept(q: JoinQueue, rds: Redis, scope: str) -> None:
    assert await q.offer_next(scope, 1) is None
    await q.enqueue(scope, "a")
    await q.enqueue(scope, "b")
    offered = await q.offer_next(scope, 3)
    assert offered is not None
    user, deadline = offered
    assert user == "a"
    ttl = int(await rds.pttl(f"offer:{scope}:a"))
    assert 0 < ttl <= 30_000
    assert await q.length(scope) == 1
    assert await q.accept(scope, "a") == 3
    assert await q.accept(scope, "a") is None
    assert await rds.zscore(f"offers:{scope}", "a") is None
    assert deadline > 0


async def test_sweep_requeues_at_original_score_then_drops(
    q: JoinQueue, scope: str
) -> None:
    await q.enqueue(scope, "a")
    await q.enqueue(scope, "b")
    offered = await q.offer_next(scope, 1)
    assert offered is not None
    deadline = offered[1]
    await q.enqueue(scope, "c")
    assert await q.sweep(scope, now_ms=deadline - 1) == []
    assert await q.sweep(scope, now_ms=deadline) == ["a"]
    assert await q.position(scope, "a") == 1
    assert await q.position(scope, "c") == 3
    # second miss: dropped
    offered = await q.offer_next(scope, 1)
    assert offered is not None
    assert offered[0] == "a"
    assert await q.sweep(scope, now_ms=offered[1]) == []
    assert await q.position(scope, "a") is None
    assert await q.accept(scope, "a") is None
    assert await q.position(scope, "b") == 1


async def test_sweep_skips_accepted(q: JoinQueue, scope: str) -> None:
    await q.enqueue(scope, "a")
    offered = await q.offer_next(scope, 1)
    assert offered is not None
    assert await q.accept(scope, "a") == 1
    assert await q.sweep(scope, now_ms=offered[1]) == []


async def test_accept_fails_once_sweep_claims_it_first(
    q: JoinQueue, scope: str
) -> None:
    """The reverse order of ``test_sweep_skips_accepted``: whichever of the two
    claims the offer first through ``_claim_offer`` wins, the other must not
    also succeed (a racing accept must never both grant a seat AND leave the
    user requeued/miss-counted by a concurrent sweep)."""
    await q.enqueue(scope, "a")
    await q.enqueue(scope, "b")
    offered = await q.offer_next(scope, 1)
    assert offered is not None
    assert await q.sweep(scope, now_ms=offered[1]) == ["a"]  # sweep wins first
    assert await q.accept(scope, "a") is None  # too late: nothing left to claim
    assert await q.position(scope, "a") == 1  # requeued, not silently dropped


def test_eta_arithmetic() -> None:
    assert JoinQueue.eta_seconds(1, None) is None
    started = datetime.now(UTC) - timedelta(seconds=300)
    assert JoinQueue.eta_seconds(1, started, typical_game_s=900) == pytest.approx(
        600, abs=2
    )
    assert JoinQueue.eta_seconds(3, started, typical_game_s=900) == pytest.approx(
        2400, abs=2
    )
    assert (
        JoinQueue.eta_seconds(2, started - timedelta(hours=1), typical_game_s=900)
        == 900
    )


async def test_snapshot(q: JoinQueue, scope: str) -> None:
    await q.enqueue(scope, "a")
    await q.enqueue(scope, "b")
    started = datetime.now(UTC)
    snap = await q.snapshot(scope, "b", started)
    assert isinstance(snap, QueueStatus)
    assert snap.position == 2
    assert snap.length == 2
    assert snap.eta_seconds == pytest.approx(1800, abs=2)
    assert (await q.snapshot(scope, "zz")).model_dump() == {
        "position": None,
        "length": 2,
        "eta_seconds": None,
    }
