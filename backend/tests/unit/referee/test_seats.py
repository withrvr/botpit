"""``HumanController`` queue semantics (Phase 8 review fix): bounded to the
latest not-yet-consumed submission, so a client hammering ``submit`` cannot
grow the queue without limit."""

from __future__ import annotations

import asyncio

from app.referee.seats import HumanController


async def test_submit_replaces_a_not_yet_consumed_move() -> None:
    human = HumanController()
    human.submit({"which": "first"})
    human.submit({"which": "second"})
    # `submit` schedules via `call_soon_threadsafe`; give the loop a turn.
    await asyncio.sleep(0)

    move = await asyncio.wait_for(human._pending.get(), 1)
    assert move == {"which": "second"}
    assert human._pending.empty()


async def test_submit_never_exceeds_maxsize_one() -> None:
    human = HumanController()
    for i in range(20):
        human.submit({"which": i})
        await asyncio.sleep(0)

    assert human._pending.qsize() <= 1
