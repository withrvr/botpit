"""Owns the asyncio task per running game. Phase 7 rooms sit on top of this."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
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

    def start(
        self,
        room_id: str,
        slug: str,
        seed: int,
        specs: Sequence[SeatSpec],
        controllers: Mapping[Seat, SeatController],
    ) -> RunningGame:
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
        return running

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
