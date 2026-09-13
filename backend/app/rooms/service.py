"""Room lifecycle: create, join, leave, start, stop (docs/rooms.md).

Plain exceptions here, not ``HTTPException``: the route layer translates them,
matching ``app.api.routes.admin``'s ``except LookupError as e: raise
HTTPException(...)`` pattern.

Async/sync split: methods that only touch the database stay synchronous
(``get``, ``list_public``, ``leave``); methods that publish an event, touch
the join queue, or drive ``GameRunner`` are ``async def`` (``create``,
``join_by_code``, ``capacity_check``, ``force_start``, ``start``, ``stop``).
The build brief sketched a couple of these as plain ``def``; where a method
has to ``await`` something (a bus publish, a queue snapshot, or another async
service method) it is ``async`` here instead -- see the PROGRESS.md/report
notes for the full list of these judgment calls.
"""

from __future__ import annotations

import contextlib
import secrets
import uuid
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app import settings_store
from app.agents import AgentController, get_persona
from app.authz.service import grant_role
from app.events import PUBLIC, EventBus, RoomCreated, SeatFilled, make_event
from app.games import Seat, get_game
from app.models import Room, RoomCreate, RoomSeat, User, get_datetime_utc
from app.referee import GameRunner, HumanController, SeatController, SeatSpec
from app.rooms.codes import new_join_code, normalise_code
from app.rooms.queue import JoinQueue, QueueStatus

if TYPE_CHECKING:
    from app.llm import ModelRegistry

_MAX_CODE_ATTEMPTS = 10
# ponytail: no user is actually enqueued at create-time (capacity_check only
# reads the queue for its length/eta), so any key is fine here -- position is
# always None. Real per-user positions belong to a future enqueue-on-capacity
# flow; not asked for by this phase.
_CAPACITY_PROBE_KEY = "capacity-probe"


class RoomError(Exception):
    """Base for room-service errors; the route layer maps these to HTTP."""


class RoomFullError(RoomError):
    """No open seat (join) or an open seat remains (start).

    ``room_id`` is set by ``join_by_code`` so the route can attach queue
    info to the 409 body; ``start`` raises it without a ``room_id`` since
    that 409 needs no queue info (the caller already has the room id).
    """

    def __init__(self, message: str, room_id: uuid.UUID | None = None) -> None:
        super().__init__(message)
        self.room_id = room_id


class RoomNotWaitingError(RoomError):
    """The room has already started, finished, or been stopped."""


class CapacityExceededError(RoomError):
    def __init__(self, scope: str, status: QueueStatus) -> None:
        super().__init__(f"capacity exceeded: {scope}")
        self.scope = scope
        self.status = status


async def capacity_exceeded_handler(_: Request, exc: Exception) -> JSONResponse:
    """Registered on the app (``app.main``), matching ``rate_limited_handler``:
    the 429 body is ``{"detail": ..., "queue": {"position", "length", "eta_seconds"}}``,
    a shape plain ``HTTPException(detail=...)`` cannot produce without nesting."""
    assert isinstance(exc, CapacityExceededError)
    return JSONResponse(
        {"detail": str(exc), "queue": exc.status.model_dump()},
        status_code=429,
    )


class RoomService:
    def __init__(
        self,
        session: Session,
        runner: GameRunner,
        queue: JoinQueue,
        registry: ModelRegistry,
        bus: EventBus,
    ) -> None:
        self.session = session
        self.runner = runner
        self.queue = queue
        self.registry = registry
        self.bus = bus

    # -- capacity -----------------------------------------------------------

    async def capacity_check(self) -> None:
        """Raises ``CapacityExceededError`` when the global concurrent-game
        cap is at or over its limit."""
        count = self.session.exec(
            select(func.count()).select_from(Room).where(Room.status == "in_progress")
        ).one()
        limit = settings_store.max_concurrent_games(self.session)
        if count >= limit:
            status = await self.queue.snapshot("global", _CAPACITY_PROBE_KEY)
            raise CapacityExceededError("global", status)

    # -- create ---------------------------------------------------------------

    async def create(self, creator: User, spec: RoomCreate) -> Room:
        await self.capacity_check()
        try:
            game = get_game(spec.game)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        game.check_seat_count(spec.seat_count)  # raises ValueError

        # Two passes: validate everything first (so a bad seat 422s before any
        # row is written), then build the actual `RoomSeat` rows once the room
        # (and its id) exists.
        resolved_models: list[str | None] = []
        resolved_personas: list[str | None] = []
        for seat_spec in spec.seats:
            if seat_spec.kind == "agent":
                model = seat_spec.model or self.registry.default_model
                if model not in self.registry.models:
                    msg = (
                        f"unknown model {model!r}; known: "
                        f"{', '.join(self.registry.models)}"
                    )
                    raise ValueError(msg)
                persona_name = seat_spec.persona or "steady"
                try:
                    get_persona(persona_name)
                except KeyError as exc:
                    raise ValueError(str(exc)) from exc
                resolved_models.append(model)
                resolved_personas.append(persona_name)
            else:
                for field_name in ("user_id", "invited_user_id"):
                    uid = getattr(seat_spec, field_name)
                    if uid is not None and self.session.get(User, uid) is None:
                        raise ValueError(f"unknown user: {uid}")
                resolved_models.append(None)
                resolved_personas.append(None)

        room = self._insert_room_with_unique_code(creator, spec)
        for i, seat_spec in enumerate(spec.seats):
            if seat_spec.kind == "agent":
                self.session.add(
                    RoomSeat(
                        room_id=room.id,
                        seat=i,
                        kind="agent",
                        model=resolved_models[i],
                        persona=resolved_personas[i],
                        status="filled",
                    )
                )
            else:
                self.session.add(
                    RoomSeat(
                        room_id=room.id,
                        seat=i,
                        kind="human",
                        user_id=seat_spec.user_id,
                        invited_user_id=seat_spec.invited_user_id,
                        status="filled" if seat_spec.user_id else "open",
                    )
                )
        self.session.commit()

        grant_role(self.session, creator.id, "moderator", room.id)
        await self.bus.publish(
            make_event(
                str(room.id),
                RoomCreated(game=spec.game, seats=spec.seat_count),
                PUBLIC,
            )
        )
        self.session.refresh(room)
        return room

    def _insert_room_with_unique_code(self, creator: User, spec: RoomCreate) -> Room:
        for _ in range(_MAX_CODE_ATTEMPTS):
            room = Room(
                code=new_join_code(),
                game=spec.game,
                seat_count=spec.seat_count,
                visibility=spec.visibility,
                creator_id=creator.id,
                seed=secrets.randbits(63),
            )
            self.session.add(room)
            try:
                self.session.commit()
            except IntegrityError:
                self.session.rollback()
                continue
            return room
        msg = "could not allocate a unique room code"
        raise RuntimeError(msg)

    # -- reads ----------------------------------------------------------------

    def get(self, room_id: uuid.UUID) -> Room:
        room = self.session.get(Room, room_id)
        if room is None:
            raise LookupError(f"unknown room: {room_id}")
        return room

    def list_public(self, limit: int = 50, offset: int = 0) -> tuple[list[Room], int]:
        count = self.session.exec(
            select(func.count()).select_from(Room).where(Room.visibility == "public")
        ).one()
        rows = self.session.exec(
            select(Room)
            .where(Room.visibility == "public")
            .order_by(col(Room.created_at).desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), count

    def seats(self, room_id: uuid.UUID) -> list[RoomSeat]:
        return list(
            self.session.exec(
                select(RoomSeat)
                .where(RoomSeat.room_id == room_id)
                .order_by(col(RoomSeat.seat))
            ).all()
        )

    # -- join / leave -----------------------------------------------------

    async def join_by_code(self, user: User, code: str) -> RoomSeat:
        normalised = normalise_code(code)
        room = self.session.exec(select(Room).where(Room.code == normalised)).first()
        if room is None:
            raise LookupError(f"unknown room code: {code}")
        if room.status != "waiting":
            raise RoomNotWaitingError(f"room {room.id} is {room.status}")

        seats = self.seats(room.id)
        already = next(
            (s for s in seats if s.kind == "human" and s.user_id == user.id), None
        )
        if already is not None:
            return already

        target = next(
            (
                s
                for s in seats
                if s.kind == "human"
                and s.status == "open"
                and s.invited_user_id == user.id
            ),
            None,
        ) or next((s for s in seats if s.kind == "human" and s.status == "open"), None)
        if target is None:
            raise RoomFullError(f"room {room.id} has no open seat", room_id=room.id)

        target.status = "filled"
        target.user_id = user.id
        target.invited_user_id = None
        self.session.add(target)
        self.session.commit()
        self.session.refresh(target)
        await self.bus.publish(
            make_event(
                str(room.id),
                SeatFilled(seat=target.seat, player=str(user.id)),
                PUBLIC,
            )
        )
        return target

    def leave(self, user: User, room_id: uuid.UUID) -> None:
        room = self.get(room_id)
        if room.status != "waiting":
            return
        seat = next(
            (
                s
                for s in self.seats(room_id)
                if s.kind == "human" and s.user_id == user.id
            ),
            None,
        )
        if seat is None:
            return
        seat.status = "open"
        seat.user_id = None
        self.session.add(seat)
        self.session.commit()

    # -- start / stop -------------------------------------------------------

    async def force_start(self, user: User, room_id: uuid.UUID) -> Room:
        del user  # authorised by the caller (route); nothing more to check here
        for seat in self.seats(room_id):
            if seat.kind == "human" and seat.status == "open":
                seat.kind = "agent"
                seat.model = self.registry.default_model
                seat.persona = "steady"
                seat.status = "filled"
                seat.user_id = None
                seat.invited_user_id = None
                self.session.add(seat)
        self.session.commit()
        return await self.start(room_id)

    async def start(self, room_id: uuid.UUID) -> Room:
        room = self.get(room_id)
        if room.status != "waiting":
            raise RoomNotWaitingError(f"room {room_id} is {room.status}")
        seats = self.seats(room_id)
        if any(s.status == "open" for s in seats):
            raise RoomFullError(
                f"room {room_id} still has an open seat; force_start or fill it first"
            )

        game = get_game(room.game)
        specs: list[SeatSpec] = []
        controllers: dict[Seat, SeatController] = {}
        for s in seats:
            if s.kind == "human":
                specs.append(SeatSpec(seat=s.seat, kind="human", player=str(s.user_id)))
                controllers[s.seat] = HumanController()
            else:
                model = s.model or self.registry.default_model
                persona_name = s.persona or "steady"
                specs.append(
                    SeatSpec(
                        seat=s.seat,
                        kind="agent",
                        player=f"agent:{model}",
                        model=model,
                        persona=persona_name,
                    )
                )
                controllers[s.seat] = AgentController(
                    game,
                    self.registry.client_for(model),
                    model,
                    get_persona(persona_name),
                    self.bus,
                    str(room_id),
                    s.seat,
                )

        self.runner.start(str(room_id), room.game, room.seed, specs, controllers)
        room.status = "in_progress"
        room.started_at = get_datetime_utc()
        self.session.add(room)
        self.session.commit()
        self.session.refresh(room)
        return room

    async def stop(self, room_id: uuid.UUID, reason: str) -> Room:
        room = self.get(room_id)
        # never started, or already stopped: a no-op, not an error
        with contextlib.suppress(KeyError):
            await self.runner.stop(str(room_id), reason)
        room.status = "stopped"
        room.finished_at = get_datetime_utc()
        self.session.add(room)
        self.session.commit()
        self.session.refresh(room)
        return room
