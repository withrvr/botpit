"""Owns the asyncio task per running game. Phase 7 rooms sit on top of this."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.events import EventBus
from app.games import get_game
from app.games.base import AnyGame, GameState, Seat
from app.referee.referee import Referee
from app.referee.seats import HumanController, SeatController, SeatSpec


@dataclass
class RunningGame:
    room_id: str
    game: AnyGame
    referee: Referee
    task: asyncio.Task[GameState]
    specs: tuple[SeatSpec, ...]
    controllers: dict[Seat, SeatController] = field(default_factory=dict)

    @property
    def state(self) -> GameState:
        return self.referee.state

    @property
    def done(self) -> bool:
        return self.task.done()


class GameRunner:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._games: dict[str, RunningGame] = {}
        # Strong refs to the watcher tasks `start(on_finished=...)` spawns: asyncio
        # only holds a weak ref to a task nothing else references, so without this
        # a watcher could be garbage-collected mid-flight (see asyncio.create_task
        # docs). Self-cleaned in `_watch` once the game it watches is done.
        self._watchers: dict[str, asyncio.Task[None]] = {}

    def start(
        self,
        room_id: str,
        slug: str,
        seed: int,
        specs: Sequence[SeatSpec],
        controllers: Mapping[Seat, SeatController],
        *,
        on_finished: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> RunningGame:
        """``on_finished(room_id, outcome)`` fires exactly once when the game's
        task ends, with ``outcome`` "finished" (the referee returned normally)
        or "stopped" (cancelled, or the referee raised). This is the only place
        that knows a game actually ended; callers (``RoomService``) use it to
        keep a room's persisted status from diverging from what is really
        running -- ``stop()`` cancelling the task also satisfies this callback,
        so a caller does not need to update status from both places.
        """
        if room_id in self._games and not self._games[room_id].done:
            msg = f"room {room_id} already has a running game"
            raise ValueError(msg)
        game = get_game(slug)
        referee = Referee(game, self.bus, room_id, seed, specs, controllers)
        task = asyncio.create_task(referee.run(), name=f"game:{room_id}")
        running = RunningGame(
            room_id=room_id,
            game=game,
            referee=referee,
            task=task,
            specs=tuple(specs),
            controllers=dict(controllers),
        )
        self._games[room_id] = running
        if on_finished is not None:
            self._watchers[room_id] = asyncio.create_task(
                self._watch(room_id, task, on_finished), name=f"game-watch:{room_id}"
            )
        return running

    async def _watch(
        self,
        room_id: str,
        task: asyncio.Task[GameState],
        on_finished: Callable[[str, str], Awaitable[None]],
    ) -> None:
        outcome = "finished"
        try:
            await task
        except asyncio.CancelledError:
            outcome = "stopped"
        except Exception:  # a game/controller bug ended the room; still report it
            outcome = "stopped"
        self._watchers.pop(room_id, None)
        # Without this, `get()`/`submit_move()` keep finding this room's dead
        # game forever: `current_seat` on a terminal state is often stable
        # (or `None`, which trivially fails a `!=` turn check), so a move
        # submitted after the game ends can look "accepted" with nobody left
        # to read it, and `/view` never falls back to a 409. Also a
        # per-room memory leak (RunningGame holds the referee, controllers,
        # and final state for the process's lifetime).
        self._games.pop(room_id, None)
        with contextlib.suppress(Exception):
            await on_finished(room_id, outcome)

    def get(self, room_id: str) -> RunningGame:
        return self._games[room_id]

    def active(self) -> list[RunningGame]:
        return [g for g in self._games.values() if not g.done]

    async def stop(self, room_id: str, reason: str = "stopped") -> None:
        running = self._games[room_id]
        if running.done:
            return
        running.task.cancel(reason)
        with contextlib.suppress(asyncio.CancelledError):
            await running.task

    def submit_move(self, room_id: str, seat: Seat, move: dict[str, Any]) -> None:
        controller = self._games[room_id].controllers[seat]
        if not isinstance(controller, HumanController):
            msg = f"seat {seat} is not human-controlled"
            raise TypeError(msg)
        controller.submit(move)

    async def close(self) -> None:
        for room_id in list(self._games):
            await self.stop(room_id, "shutdown")
        # `stop()` only awaits the game task itself; give each watcher (a
        # separately scheduled task reacting to that same task) a turn to run
        # its `on_finished` callback before shutdown proceeds.
        for watcher in list(self._watchers.values()):
            with contextlib.suppress(Exception):
                await watcher
