"""Seat controllers: the one interface between the referee and whoever plays."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol

from app.events.schema import ChatMessage
from app.games.base import AnyGame, Frozen, GameState, Move, PlayerView, Seat

SeatKind = Literal["human", "agent", "scripted"]


class SeatSpec(Frozen):
    seat: Seat
    kind: SeatKind
    player: str
    model: str | None = None
    persona: str | None = None
    timeout_s: float = 30.0
    # Agents and scripts get this many rejected attempts before the referee
    # plays for them; humans retry until the turn deadline.
    max_retries: int = 2


@dataclass(frozen=True)
class TurnContext:
    room_id: str
    seat: Seat
    turn_id: str
    attempt: int
    deadline: float  # event-loop time (``loop.time()``)
    last_error: str | None
    recent_chat: tuple[ChatMessage, ...] = ()


class ControllerError(RuntimeError):
    """A controller could not produce a move at all (script exhausted, model down)."""


class SeatController(Protocol):
    kind: ClassVar[SeatKind]

    async def decide(self, view: PlayerView, ctx: TurnContext) -> dict[str, Any]:
        """Return the move as a plain dict; the referee validates it."""
        ...


class ScriptedController:
    """Plays a fixed list of move dicts, then raises ``ControllerError``.

    Every ``decide`` call consumes one entry, including calls made after a
    rejection: a rejected scripted move is not retried, the next one is played.
    """

    kind: ClassVar[SeatKind] = "scripted"

    def __init__(self, moves: Sequence[dict[str, Any]]) -> None:
        self._moves = list(moves)
        self.calls = 0

    async def decide(self, view: PlayerView, ctx: TurnContext) -> dict[str, Any]:
        self.calls += 1
        if not self._moves:
            raise ControllerError("script exhausted")
        return self._moves.pop(0)


class PolicyController:
    """Chooses from the view with a callable; the default plays the first legal move."""

    kind: ClassVar[SeatKind] = "scripted"

    def __init__(
        self, policy: Callable[[PlayerView, TurnContext], dict[str, Any]] | None = None
    ) -> None:
        self._policy = policy or (lambda view, _ctx: view.legal_moves[0])
        self.calls = 0

    async def decide(self, view: PlayerView, ctx: TurnContext) -> dict[str, Any]:
        self.calls += 1
        return self._policy(view, ctx)


class HumanController:
    """Waits for a move pushed by the transport layer (websocket, CLI, test)."""

    kind: ClassVar[SeatKind] = "human"

    def __init__(self) -> None:
        # maxsize=1: only the latest not-yet-consumed submission is ever
        # meaningful for a single turn -- `_put` below replaces rather than
        # queues, so this bounds memory regardless of how many times a
        # client calls `submit` before the referee drains one.
        self._pending: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self.view: PlayerView | None = None
        self.last_error: str | None = None
        # Captured here because construction always happens on the event
        # loop thread (inside `RoomService.start`, an `async def`); `submit`
        # is reached from an HTTP route running in Starlette's threadpool, a
        # different OS thread from the referee's `decide()` awaiting this
        # same queue -- `asyncio.Queue` is not safe for a cross-thread
        # `put_nowait`, only for `call_soon_threadsafe`.
        self._loop = asyncio.get_running_loop()

    def submit(self, move: dict[str, Any]) -> None:
        self._loop.call_soon_threadsafe(self._put, move)

    def _put(self, move: dict[str, Any]) -> None:
        """Runs on the loop thread. The latest submission always wins over
        one not yet consumed -- never grows unbounded no matter how many
        times a client calls `submit` in one turn."""
        while not self._pending.empty():
            self._pending.get_nowait()
        self._pending.put_nowait(move)

    def begin_turn(self) -> None:
        """Drop moves submitted for an earlier turn."""
        while not self._pending.empty():
            self._pending.get_nowait()

    def on_rejected(self, reason: str) -> None:
        """Called by the referee the moment a submitted move is refused."""
        self.last_error = reason

    async def decide(self, view: PlayerView, ctx: TurnContext) -> dict[str, Any]:
        self.view = view
        return await self._pending.get()


def fallback_move(game: AnyGame, state: GameState, seat: Seat) -> Move:
    """Deterministic move the referee plays when a seat cannot: the first legal one.

    Games order ``legal_moves`` so the first entry is the least committal
    (draw or pass where allowed), which keeps a stuck seat from being punished.
    """
    moves: list[Move] = game.legal_moves(state, seat)
    if not moves:
        msg = f"no legal move for seat {seat}"
        raise RuntimeError(msg)
    return moves[0]
