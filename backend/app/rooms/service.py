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
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import CursorResult, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app import settings_store
from app.agents import AgentController, get_persona
from app.api.views import view_payload
from app.authz.service import grant_role
from app.core.db import engine
from app.events import (
    PUBLIC,
    ChatMessage,
    EventBus,
    RoomCreated,
    SeatFilled,
    make_event,
)
from app.games import Seat, get_game
from app.metrics import ACTIVE_GAMES
from app.models import (
    GameEvent,
    GameRecord,
    Room,
    RoomCreate,
    RoomSeat,
    User,
    get_datetime_utc,
)
from app.referee import GameRunner, HumanController, SeatController, SeatSpec
from app.rooms.codes import new_join_code, normalise_code
from app.rooms.queue import JoinQueue, QueueStatus
from app.telemetry import get_logger

if TYPE_CHECKING:
    from app.llm import ModelRegistry

log = get_logger("rooms")

_MAX_CODE_ATTEMPTS = 10
# ponytail: no user is actually enqueued at create-time (capacity_check only
# reads the queue for its length/eta), so any key is fine here -- position is
# always None. Real per-user positions belong to a future enqueue-on-capacity
# flow; not asked for by this phase.
_CAPACITY_PROBE_KEY = "capacity-probe"
# Postgres advisory lock (session/xact-scoped) serialising the
# check-capacity-then-flip-to-in_progress critical section in `start()` across
# every worker process, not just this one -- see `start()` for why a Python
# lock alone would not be enough.
_START_LOCK_KEY = 0x726F6F6D_73746172  # arbitrary bigint, "room" + "star" as bytes
_ROOM_CODE_UNIQUE_INDEX = "ix_room_code"
_MAX_CHAT_CHARS = 200


def _is_code_collision(exc: IntegrityError) -> bool:
    """True only for a violation of the room-code unique index, not any other
    constraint an insert happens to hit (a bad FK, say) -- those must not be
    silently retried and reported as the misleading "no code available"."""
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name is not None:
        return bool(name == _ROOM_CODE_UNIQUE_INDEX)
    return _ROOM_CODE_UNIQUE_INDEX in str(exc.orig or exc)


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


class NotSeatedError(RoomError):
    """The caller holds no human seat in this room."""


class RoomNotRunningError(RoomError):
    """No live game for this room under ``GameRunner`` (never started,
    finished, or stopped) -- there is no ``GameState`` to read or feed."""


class NotYourTurnError(RoomError):
    """The caller's seat is not ``current_seat`` right now. Submitting
    anyway would just queue silently and be dropped by the next turn's
    ``HumanController.begin_turn()``; this gives an immediate, clear 409
    instead of that silent drop."""


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
                    log.info(
                        "room create rejected: unknown model %r (known: %s)",
                        model,
                        ", ".join(self.registry.models),
                    )
                    raise ValueError(f"unknown model: {model}")
                persona_name = seat_spec.persona or "steady"
                try:
                    get_persona(persona_name)
                except KeyError as exc:
                    raise ValueError(str(exc)) from exc
                resolved_models.append(model)
                resolved_personas.append(persona_name)
            else:
                # A seat's `user_id` may only ever be the creator seating
                # themself -- seating anyone else without their consent would
                # be mass assignment (the seat would show as "filled" with no
                # accept/decline step). Seating someone else goes through
                # `invited_user_id` + that person calling `join_by_code`.
                if seat_spec.user_id is not None and seat_spec.user_id != creator.id:
                    raise ValueError(
                        "a seat's user_id may only be the creator's own id; "
                        "invite another user via invited_user_id instead"
                    )
                if (
                    seat_spec.invited_user_id is not None
                    and self.session.get(User, seat_spec.invited_user_id) is None
                ):
                    raise ValueError(f"unknown user: {seat_spec.invited_user_id}")
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
            except IntegrityError as exc:
                self.session.rollback()
                if not _is_code_collision(exc):
                    raise  # some other constraint failed; don't mask it as "no code"
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

    # -- game history (Phase 9): the durable record `_archive_game` writes --

    def get_game_record(self, game_id: str) -> GameRecord:
        record = self.session.get(GameRecord, game_id)
        if record is None:
            raise LookupError(f"unknown game: {game_id}")
        return record

    def list_game_records(self, room_id: uuid.UUID) -> list[GameRecord]:
        return list(
            self.session.exec(
                select(GameRecord)
                .where(GameRecord.room_id == room_id)
                .order_by(col(GameRecord.finished_at).desc())
            ).all()
        )

    def game_events(
        self, game_id: str, since: int | None = None, limit: int = 200
    ) -> list[GameEvent]:
        query = select(GameEvent).where(GameEvent.game_id == game_id)
        if since is not None:
            query = query.where(col(GameEvent.seq) > since)
        return list(
            self.session.exec(query.order_by(col(GameEvent.seq)).limit(limit)).all()
        )

    # -- join / leave -----------------------------------------------------

    async def join_by_code(self, user: User, code: str) -> RoomSeat:
        normalised = normalise_code(code)
        room = self.session.exec(select(Room).where(Room.code == normalised)).first()
        if room is None:
            raise LookupError(f"unknown room code: {code}")
        if room.status != "waiting":
            raise RoomNotWaitingError(f"room {room.id} is {room.status}")

        already = next(
            (
                s
                for s in self.seats(room.id)
                if s.kind == "human" and s.user_id == user.id
            ),
            None,
        )
        if already is not None:
            return already

        target = self._claim_open_seat(room.id, user.id)
        if target is None:
            raise RoomFullError(f"room {room.id} has no open seat", room_id=room.id)

        await self.bus.publish(
            make_event(
                str(room.id),
                SeatFilled(seat=target.seat, player=str(user.id)),
                PUBLIC,
            )
        )
        return target

    def _claim_open_seat(
        self, room_id: uuid.UUID, user_id: uuid.UUID
    ) -> RoomSeat | None:
        """Atomically fill the best open seat for ``user_id``: their own
        reserved invite first, else any seat not reserved for someone else
        (a seat still `invited_user_id`-reserved for a different user is
        never taken by the fallback -- that would silently lock the intended
        invitee out).

        The UPDATE's ``WHERE`` clause, not the prior read, is what actually
        prevents two concurrent callers from both filling the same seat:
        Postgres serialises concurrent UPDATE statements to the same row, so exactly
        one commits with ``rowcount == 1`` and the loser retries against
        whatever is left, rather than both believing they won a Python-level
        check-then-write race.
        """
        for _ in range(_MAX_CODE_ATTEMPTS):
            seats = self.seats(room_id)
            candidate = next(
                (
                    s
                    for s in seats
                    if s.kind == "human"
                    and s.status == "open"
                    and s.invited_user_id == user_id
                ),
                None,
            ) or next(
                (
                    s
                    for s in seats
                    if s.kind == "human"
                    and s.status == "open"
                    and s.invited_user_id is None
                ),
                None,
            )
            if candidate is None:
                return None
            # A Core `UPDATE ... WHERE` (not a `select()`) is exactly the
            # atomicity primitive this needs, and the row count it returns is
            # only available via the plain `execute()`, not `exec()`.
            result = self.session.execute(  # ty: ignore[deprecated]
                update(RoomSeat)
                .where(
                    col(RoomSeat.id) == candidate.id,
                    col(RoomSeat.status) == "open",
                    or_(
                        col(RoomSeat.invited_user_id).is_(None),
                        col(RoomSeat.invited_user_id) == user_id,
                    ),
                )
                .values(status="filled", user_id=user_id, invited_user_id=None)
            )
            self.session.commit()
            rowcount = cast("CursorResult[Any]", result).rowcount
            if rowcount == 1:
                self.session.refresh(candidate)
                return candidate
            # lost the race for this seat (or it just changed underfoot); retry
        return None

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
        # Staged, not committed: if `start()` below raises (room full some
        # other way, capacity exceeded, ...), these human seats must not be
        # left permanently converted to agent seats with the room stuck
        # "waiting". `start()`'s own `self.seats(room_id)` query autoflushes
        # this session's pending changes first, so it still sees the backfill;
        # everything commits together in `start()`'s single final commit, or
        # none of it does if `start()` raises and the session later rolls back.
        for seat in self.seats(room_id):
            if seat.kind == "human" and seat.status == "open":
                seat.kind = "agent"
                seat.model = self.registry.default_model
                seat.persona = "steady"
                seat.status = "filled"
                seat.user_id = None
                seat.invited_user_id = None
                self.session.add(seat)
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

        # Serialises the check-capacity-then-flip-to-"in_progress" step across
        # every worker process (a Postgres advisory lock, not a Python one, so
        # it holds even if this app ever runs with more than one worker).
        # `capacity_check()` alone (also used by `create()`) only reads the
        # current count; without this lock two concurrent `start()` calls
        # could both pass that read before either commits, overshooting
        # `max_concurrent_games`. Xact-scoped: released automatically at the
        # commit/rollback that ends this method.
        # `session.exec()` is SQLModel's wrapper for `select()` returning model
        # rows; a bare `text()` DB call has no model to return, so the plain
        # SQLAlchemy `execute()` is the only option here.
        self.session.execute(  # ty: ignore[deprecated]
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _START_LOCK_KEY}
        )
        await self.capacity_check()
        # Re-check status: something else could have started or stopped this
        # exact room while we were building specs or waiting for the lock.
        self.session.refresh(room)
        if room.status != "waiting":
            raise RoomNotWaitingError(f"room {room_id} is {room.status}")

        try:
            self.runner.start(
                str(room_id),
                room.game,
                room.seed,
                specs,
                controllers,
                on_finished=self._finish_room,
            )
        except ValueError as exc:
            # GameRunner's own "already running" guard; surfaced as the same
            # 409 the rest of this method uses for a room that isn't "waiting".
            raise RoomNotWaitingError(str(exc)) from exc
        room.status = "in_progress"
        room.started_at = get_datetime_utc()
        self.session.add(room)
        self.session.commit()
        self.session.refresh(room)
        ACTIVE_GAMES.inc()
        return room

    async def _finish_room(self, room_id: str, outcome: str) -> None:
        """``GameRunner``'s completion callback (``outcome`` is "finished" or
        "stopped"). Runs long after the request that called ``start()`` has
        ended and its ``Session`` closed, so it opens its own. Only *writes*
        a room still "in_progress": ``stop()`` races this exact callback (its
        own ``await runner.stop(...)`` and this watcher both wake on the same
        cancelled task), and whichever already wrote "stopped" must not be
        clobbered or double-written.

        Archiving (below) is NOT inside that guard: this callback fires
        exactly once per game no matter which side won the status-write
        race, so archiving must run unconditionally -- nesting it inside the
        guard would silently skip archiving every time ``stop()`` happened
        to win, which is exactly the path ``docs/games.md``'s own tests
        exercise. It is also a separate, best-effort step: the room's
        lifecycle status must never stay stuck "in_progress" just because
        archiving hit a DB hiccup -- that would reopen the exact capacity
        leak Phase 7 fixed, for a much worse reason.
        """
        with Session(engine) as session:
            room = session.get(Room, uuid.UUID(room_id))
            if room is None:
                return
            # Unconditional, not nested in the status-write guard below --
            # same reasoning as archiving: this callback fires exactly once
            # per game no matter which side won the `stop()` race, so the
            # gauge must always come back down exactly once too.
            ACTIVE_GAMES.dec()
            if room.status == "in_progress":
                room.status = outcome
                room.finished_at = get_datetime_utc()
                session.add(room)
                session.commit()

            try:
                await self._archive_game(session, room, outcome)
            except Exception:
                log.exception("failed to archive game history for room %s", room_id)

    async def _archive_game(self, session: Session, room: Room, outcome: str) -> None:
        """Copies this game's event log from the live ``EventBus`` into
        ``game_record``/``game_event`` -- the durable record DECISIONS.md §12
        requires ("the event log is the source of truth"). The live bus
        (Redis or in-memory) is a transport, not durable storage: this is
        what survives a Redis flush or an ``InMemoryEventBus`` restart.

        Scoped to events carrying a ``game_id`` correlation, i.e. everything
        the ``Referee`` published -- ``game.*``/``agent.*``/``chat.*``, and
        also its own ``room.stopped`` (published on cancellation, still part
        of this game's run). ``RoomService``'s own lifecycle events
        (``room.created``, ``room.seat_filled``) carry no ``game_id`` and are
        excluded -- they already have their own row in ``room``/``room_seat``.
        """
        events = await self.bus.history(str(room.id), since=None, limit=100_000)
        game_events = [e for e in events if e.correlation.get("game_id")]
        if not game_events:
            return  # GameRunner always publishes GameStarted first; defensive only
        game_id = game_events[0].correlation["game_id"]

        started = next((e for e in game_events if e.type == "game.started"), None)
        finished = next((e for e in game_events if e.type == "game.finished"), None)
        move_count = sum(1 for e in game_events if e.type == "game.move_applied")

        record = GameRecord(
            id=game_id,
            room_id=room.id,
            game=started.payload["game"] if started else room.game,
            seat_count=started.payload["seats"] if started else room.seat_count,
            seed=started.payload["seed"] if started else room.seed,
            outcome=outcome,
            winner_seat=finished.payload["winner_seat"] if finished else None,
            ranking=finished.payload["ranking"] if finished else [],
            scores=finished.payload["scores"] if finished else [],
            move_count=move_count,
            started_at=room.started_at or room.created_at,
            finished_at=room.finished_at or get_datetime_utc(),
        )
        session.add(record)
        # Explicit, not left to the flush planner's dependency sort: forces
        # `game_record` to exist before the FK-referencing `game_event` rows
        # below, in the same transaction (a failure past this point rolls
        # back the whole archive, never an orphaned summary with no events).
        session.flush()
        for e in game_events:
            session.add(
                GameEvent(
                    game_id=game_id,
                    room_id=room.id,
                    seq=e.seq if e.seq is not None else 0,
                    type=e.type,
                    ts=e.ts,
                    visibility=e.visibility.model_dump(mode="json"),
                    payload=e.payload,
                    correlation=e.correlation,
                    stream_id=e.stream_id,
                )
            )
        session.commit()

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

    # -- live play (Phase 8: the minimal API a human plays through) --------

    def own_seat(self, user: User, room_id: uuid.UUID) -> RoomSeat:
        """The caller's own human seat in this room. Never trust a
        client-supplied seat number for anything -- it is always derived
        from who is actually seated, here."""
        seat = next(
            (
                s
                for s in self.seats(room_id)
                if s.kind == "human" and s.user_id == user.id
            ),
            None,
        )
        if seat is None:
            raise NotSeatedError(f"not seated in room {room_id}")
        return seat

    def live_view(self, user: User, room_id: uuid.UUID) -> dict[str, Any]:
        """The caller's own current ``PlayerView`` of the *actually running*
        game (``GameRunner``'s live ``GameState``, not anything in the
        database -- the DB only ever holds room/seat metadata)."""
        seat = self.own_seat(user, room_id)
        try:
            running = self.runner.get(str(room_id))
        except KeyError as exc:
            raise RoomNotRunningError(f"room {room_id} has no live game") from exc
        return view_payload(running.game, running.state, seat.seat)

    def submit_move(self, user: User, room_id: uuid.UUID, move: dict[str, Any]) -> None:
        """Forwards ``move`` to the caller's own seat's ``HumanController``.
        Validation and legality are the referee's job (it calls
        ``game.parse_move``/``apply`` and feeds any rejection back as a
        seat-visible ``game.move_rejected`` event, retrying); this method
        only checks identity (own seat) and turn order (a friendlier 409
        than a silently dropped, out-of-turn submission)."""
        seat = self.own_seat(user, room_id)
        try:
            running = self.runner.get(str(room_id))
        except KeyError as exc:
            raise RoomNotRunningError(f"room {room_id} has no live game") from exc
        if running.game.current_seat(running.state) != seat.seat:
            raise NotYourTurnError(f"seat {seat.seat} is not the current turn")
        self.runner.submit_move(str(room_id), seat.seat, move)

    async def send_chat(self, user: User, room_id: uuid.UUID, message: str) -> None:
        """Publishes ``message`` as a public ``chat.message`` from the
        caller's own seat (DECISIONS.md §8: downstream-only, shared between
        human and AI players). Unlike ``submit_move``, chat never touches
        game state, so this works whether or not a game is currently
        running -- there is no ``RoomNotRunningError`` gate here.

        If a game *is* running: the message is fed to every AI seat's
        ``hear()`` so personas actually react to what a human just said
        (``AgentController.hear`` existed but nothing called it before this),
        and tagged with that game's own ``correlation.game_id`` so it
        archives alongside the game it was said during
        (``RoomService._archive_game``, Phase 9) exactly like an agent's own
        chat already does (``AgentController.say``).
        """
        seat = self.own_seat(user, room_id)
        stripped = message.strip()
        if not stripped:
            msg = "chat message must not be empty"
            raise ValueError(msg)
        if len(stripped) > _MAX_CHAT_CHARS:
            msg = f"chat message must be {_MAX_CHAT_CHARS} characters or fewer"
            raise ValueError(msg)

        chat = ChatMessage(seat=seat.seat, text=stripped)
        game_id: str | None = None
        with contextlib.suppress(KeyError):
            running = self.runner.get(str(room_id))
            game_id = running.referee.game_id
            for controller in running.controllers.values():
                if isinstance(controller, AgentController):
                    controller.hear([chat])

        correlation = {"game_id": game_id} if game_id else None
        await self.bus.publish(
            make_event(str(room_id), chat, PUBLIC, correlation=correlation)
        )
