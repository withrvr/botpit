"""``GameRunner``'s completion watcher (``start(..., on_finished=...)``): the
single place that knows a game actually ended, whether it finished on its own
or was cancelled. ``RoomService`` relies on this to keep a room's persisted
status from diverging from what is really running."""

from __future__ import annotations

import asyncio

from app.events import InMemoryEventBus
from app.games import get_game
from app.referee import GameRunner, PolicyController, SeatSpec


def _specs(n: int) -> list[SeatSpec]:
    return [SeatSpec(seat=i, kind="scripted", player=f"p{i}") for i in range(n)]


async def test_on_finished_fires_with_finished_when_the_game_ends_on_its_own() -> None:
    game = get_game("hearts")
    n = game.min_seats
    runner = GameRunner(InMemoryEventBus(maxlen=10_000))
    outcomes: list[tuple[str, str]] = []

    async def on_finished(room_id: str, outcome: str) -> None:
        outcomes.append((room_id, outcome))

    running = runner.start(
        "r1",
        "hearts",
        1,
        _specs(n),
        {i: PolicyController() for i in range(n)},
        on_finished=on_finished,
    )
    await asyncio.wait_for(running.task, 30)
    # the watcher is a separately scheduled task reacting to the same one;
    # give it a couple of loop turns to actually run its callback.
    for _ in range(50):
        if outcomes:
            break
        await asyncio.sleep(0.01)

    assert outcomes == [("r1", "finished")]
    assert "r1" not in runner._watchers


async def test_on_finished_fires_with_stopped_when_cancelled() -> None:
    game = get_game("hearts")
    n = game.min_seats
    runner = GameRunner(InMemoryEventBus(maxlen=10_000))
    outcomes: list[tuple[str, str]] = []

    async def on_finished(room_id: str, outcome: str) -> None:
        outcomes.append((room_id, outcome))

    runner.start(
        "r2",
        "hearts",
        1,
        _specs(n),
        {i: PolicyController() for i in range(n)},
        on_finished=on_finished,
    )
    await runner.stop("r2", "test cleanup")
    for _ in range(50):
        if outcomes:
            break
        await asyncio.sleep(0.01)

    assert outcomes == [("r2", "stopped")]


async def test_close_waits_for_every_watcher_before_returning() -> None:
    game = get_game("hearts")
    n = game.min_seats
    runner = GameRunner(InMemoryEventBus(maxlen=10_000))
    outcomes: list[tuple[str, str]] = []

    async def on_finished(room_id: str, outcome: str) -> None:
        outcomes.append((room_id, outcome))

    runner.start(
        "r3",
        "hearts",
        1,
        _specs(n),
        {i: PolicyController() for i in range(n)},
        on_finished=on_finished,
    )
    await runner.close()
    assert outcomes == [("r3", "stopped")]
    assert runner._watchers == {}


async def test_no_watcher_spawned_without_on_finished() -> None:
    """Backwards compatible: existing callers that never pass ``on_finished``
    get no watcher and no behaviour change."""
    game = get_game("hearts")
    n = game.min_seats
    runner = GameRunner(InMemoryEventBus(maxlen=10_000))
    runner.start(
        "r4", "hearts", 1, _specs(n), {i: PolicyController() for i in range(n)}
    )
    assert runner._watchers == {}
    await runner.stop("r4", "test cleanup")
