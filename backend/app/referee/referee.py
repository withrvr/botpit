"""The turn loop."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from app.events import (
    PRIVILEGED,
    PUBLIC,
    ChatMessage,
    EventBus,
    EventEnvelope,
    GameFinished,
    GameStarted,
    MoveApplied,
    MoveRejected,
    RoomStopped,
    TurnStarted,
    TurnTimedOut,
    ViewUpdated,
    make_event,
)
from app.events import (
    seat as seat_visibility,
)
from app.games import get_game
from app.games.base import AnyGame, GameState, IllegalMoveError, Move, Seat
from app.referee.seats import (
    ControllerError,
    SeatController,
    SeatSpec,
    TurnContext,
    fallback_move,
)
from app.telemetry import get_logger, wide_event

log = get_logger("referee")


class ReplayMismatchError(RuntimeError):
    pass


class Referee:
    def __init__(
        self,
        game: AnyGame,
        bus: EventBus,
        room_id: str,
        seed: int,
        specs: Sequence[SeatSpec],
        controllers: Mapping[Seat, SeatController],
    ) -> None:
        self.game = game
        self.bus = bus
        self.room_id = room_id
        self.seed = seed
        self.specs = {s.seat: s for s in specs}
        self.controllers = dict(controllers)
        if set(self.specs) != set(self.controllers):
            msg = "every seat needs exactly one spec and one controller"
            raise ValueError(msg)
        self.state: GameState = game.new_game(seed, len(self.specs))
        self.moves: list[dict[str, Any]] = []
        self.game_id = uuid.uuid4().hex
        self.published: int = 0

    # -- public -----------------------------------------------------------

    async def run(self) -> GameState:
        with wide_event(
            "game",
            room_id=self.room_id,
            game_id=self.game_id,
            game_type=self.game.slug,
            seats=[s.model_dump(mode="json") for s in self.specs.values()],
        ) as ev:
            await self._publish(
                GameStarted(
                    game=self.game.slug,
                    seats=len(self.specs),
                    seed=self.seed,
                    seat_specs=[s.model_dump(mode="json") for s in self.specs.values()],
                ),
                PUBLIC,
            )
            await self._publish_views()
            try:
                while not self.game.is_terminal(self.state):
                    seat = self.game.current_seat(self.state)
                    assert seat is not None
                    await self._take_turn(seat)
            except asyncio.CancelledError:
                ev.set(outcome_detail="cancelled")
                await self._publish(RoomStopped(reason="cancelled"), PUBLIC)
                raise
            ranking = self.game.ranking(self.state)
            winner = ranking[0] if self.state.hands[ranking[0]] == () else None
            ev.set(turns=len(self.moves), winner=winner, ranking=ranking)
            await self._publish(
                GameFinished(
                    winner_seat=winner, ranking=ranking, scores=list(self.state.scores)
                ),
                PUBLIC,
            )
            return self.state

    @staticmethod
    def replay(
        slug: str, seed: int, seat_count: int, moves: Sequence[dict[str, Any]]
    ) -> GameState:
        """Rebuild the state from the event log's ``MoveApplied`` payloads."""
        game = get_game(slug)
        state: GameState = game.new_game(seed, seat_count)
        for index, raw in enumerate(moves):
            try:
                state = game.apply(state, game.parse_move(raw))
            except (ValidationError, IllegalMoveError) as exc:
                msg = f"move {index} does not replay: {exc}"
                raise ReplayMismatchError(msg) from exc
        return state

    # -- turn -------------------------------------------------------------

    async def _take_turn(self, seat: Seat) -> None:
        spec = self.specs[seat]
        controller = self.controllers[seat]
        turn_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        deadline = loop.time() + spec.timeout_s
        begin = getattr(controller, "begin_turn", None)
        if begin is not None:
            begin()
        with wide_event(
            "game.turn",
            room_id=self.room_id,
            game_id=self.game_id,
            turn_id=turn_id,
            seat=seat,
            seat_kind=spec.kind,
            model=spec.model,
            move_index=len(self.moves),
        ) as ev:
            await self._publish(
                TurnStarted(
                    seat=seat,
                    turn_id=turn_id,
                    deadline_ms=int(spec.timeout_s * 1000),
                    kind=spec.kind,
                ),
                PUBLIC,
            )
            move: Move | None = None
            last_error: str | None = None
            attempt = 0
            try:
                async with asyncio.timeout_at(deadline):
                    while move is None:
                        attempt += 1
                        ctx = TurnContext(
                            room_id=self.room_id,
                            seat=seat,
                            turn_id=turn_id,
                            attempt=attempt,
                            deadline=deadline,
                            last_error=last_error,
                        )
                        view = self.game.player_view(self.state, seat)
                        raw: Any = None
                        try:
                            raw = await controller.decide(view, ctx)
                            candidate = self.game.parse_move(raw)
                            if candidate.seat != seat:
                                raise IllegalMoveError(
                                    candidate, "move is for another seat"
                                )
                            # apply() is the validator; we keep the result on success
                            next_state = self.game.apply(self.state, candidate)
                        except (
                            ValidationError,
                            IllegalMoveError,
                            ControllerError,
                        ) as exc:
                            last_error = _reason(exc)
                            ev.incr("retries")
                            notify = getattr(controller, "on_rejected", None)
                            if notify is not None:
                                notify(last_error)
                            await self._publish(
                                MoveRejected(
                                    seat=seat,
                                    move=raw if isinstance(raw, dict) else None,
                                    reason=last_error,
                                    attempt=attempt,
                                ),
                                seat_visibility(seat),
                            )
                            if spec.kind != "human" and attempt > spec.max_retries:
                                ev.set(fallback=True, fallback_reason=last_error)
                                move = fallback_move(self.game, self.state, seat)
                                next_state = self.game.apply(self.state, move)
                            continue
                        move = candidate
            except TimeoutError:
                ev.set(fallback=True, fallback_reason="timeout", timed_out=True)
                move = fallback_move(self.game, self.state, seat)
                next_state = self.game.apply(self.state, move)
                await self._publish(
                    TurnTimedOut(seat=seat, fallback_move=move.model_dump(mode="json")),
                    PUBLIC,
                )
            ev.set(move=move.model_dump(mode="json"), attempts=attempt)
            await self._commit(move, next_state)

    async def _commit(self, move: Move, next_state: GameState) -> None:
        self.state = next_state
        raw = move.model_dump(mode="json")
        self.moves.append(raw)
        description = self.game.describe_move(move)
        await self._publish(
            MoveApplied(
                seat=move.seat,
                move=raw,
                description=description,
                move_index=len(self.moves) - 1,
            ),
            PUBLIC,
        )
        await self._publish_views()
        await self._table_talk(move.seat, description)

    async def _table_talk(self, seat: Seat, description: str) -> None:
        """Call 2 (DECISIONS §5): chat after the move landed, never affecting state.

        Duck-typed hooks keep ``SeatController`` at one method: a controller may
        expose ``say(view, description) -> str | None`` and ``hear(messages)``.
        """
        say = getattr(self.controllers[seat], "say", None)
        if say is None:
            return
        view = self.game.player_view(self.state, seat)
        try:
            text = await say(view, description)
        except Exception:  # chat is downstream-only: a failure must never stop the game
            log.warning("table talk failed for seat %s", seat, exc_info=True)
            return
        if not text:
            return
        message = ChatMessage(seat=seat, text=text)
        for other, controller in self.controllers.items():
            hear = getattr(controller, "hear", None)
            if other != seat and hear is not None:
                hear([message])

    async def _publish_views(self) -> None:
        for s in self.specs:
            view = self.game.player_view(self.state, s)
            await self._publish(
                ViewUpdated(seat=s, view=view.model_dump(mode="json")),
                seat_visibility(s),
            )

    async def _publish(self, payload: Any, visibility: Any) -> EventEnvelope:
        event = make_event(
            self.room_id, payload, visibility, correlation={"game_id": self.game_id}
        )
        self.published += 1
        return await self.bus.publish(event)


def _reason(exc: BaseException) -> str:
    if isinstance(exc, IllegalMoveError):
        return exc.reason
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        return f"invalid move: {first.get('msg')} at {loc or 'root'}"
    return str(exc)


__all__ = ["PRIVILEGED", "Referee", "ReplayMismatchError"]
