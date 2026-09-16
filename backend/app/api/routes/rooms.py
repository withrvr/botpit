"""Room lifecycle HTTP surface (docs/rooms.md).

Sync ``def`` routes do DB-only work (Starlette threadpools them); routes that
touch the join queue or ``GameRunner`` (join, start, force-start, stop, queue
position) are ``async def`` so they can ``await`` those calls directly.
``CapacityExceededError`` is not caught here: it is registered on the app
(``app.main``) as a global exception handler, same as ``RateLimited``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated, Any, Literal, cast

import jwt
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, ValidationError, field_validator
from sqlmodel import Session

from app.api.deps import CurrentUser, JoinQueueDep, RoomServiceDep, SessionDep
from app.authz import P, current_permissions, require_permission
from app.authz.service import load_permissions
from app.core import security
from app.core.config import settings
from app.core.redis import get_redis
from app.events import Subscriber, filter_for_subscriber
from app.games import get_game
from app.games.move_schema import move_json_schema
from app.metrics import ACTIVE_WS_CONNECTIONS
from app.models import (
    Message,
    Room,
    RoomCreate,
    RoomPublic,
    RoomSeat,
    RoomSeatJoinPublic,
    RoomSeatPublic,
    RoomsPublic,
    TokenPayload,
    User,
)
from app.ratelimit import check_rate, rate_limit_from_setting
from app.rooms.guard import resolve_subscriber, subscription_guard
from app.rooms.queue import JoinQueue, QueueStatus
from app.rooms.service import (
    NotSeatedError,
    NotYourTurnError,
    RoomFullError,
    RoomNotRunningError,
    RoomNotWaitingError,
    RoomService,
)
from app.telemetry import wide_event

router = APIRouter(prefix="/rooms", tags=["rooms"])


class RoomJoinRequest(BaseModel):
    code: str


def _seat_public(s: RoomSeat, *, reveal: bool = True) -> RoomSeatPublic:
    """``reveal=False`` blanks the seat's identity fields for a viewer who is
    neither privileged nor that seat's own occupant -- ``kind``/``seat``/
    ``status`` (is it open, filled, human, agent) stay visible either way,
    since a room's shape is not sensitive; who specifically holds a seat is.
    """
    return RoomSeatPublic(
        id=s.id,
        seat=s.seat,
        status=s.status,
        kind=cast('Literal["human", "agent"]', s.kind),
        user_id=s.user_id if reveal else None,
        invited_user_id=s.invited_user_id if reveal else None,
        model=s.model if reveal else None,
        persona=s.persona if reveal else None,
    )


def _room_public(
    room: Room,
    seats: Sequence[RoomSeat],
    *,
    privileged: bool = True,
    viewer_id: uuid.UUID | None = None,
) -> RoomPublic:
    """``privileged=False`` redacts every seat's identity except
    ``viewer_id``'s own (a caller always sees their own seat, never anyone
    else's, unless they hold ``room:view_seat_detail`` or created the room --
    both call sites that apply pass ``privileged=True``)."""
    return RoomPublic(
        id=room.id,
        code=room.code,
        game=room.game,
        seat_count=room.seat_count,
        status=room.status,
        visibility=room.visibility,
        creator_id=room.creator_id,
        created_at=room.created_at,
        started_at=room.started_at,
        finished_at=room.finished_at,
        seats=[
            _seat_public(
                s,
                reveal=privileged or (viewer_id is not None and s.user_id == viewer_id),
            )
            for s in seats
        ],
    )


def _may_operate(room: Room, user: User, perms: frozenset[str]) -> bool:
    """Creator or a room-scoped/global holder of ``room:force_stop``."""
    return room.creator_id == user.id or P.ROOM_FORCE_STOP in perms


@router.post(
    "/",
    response_model=RoomPublic,
    dependencies=[
        Depends(rate_limit_from_setting("room:create", settings.RATE_LIMIT_ROOM_CREATE))
    ],
)
async def create_room(
    *,
    service: RoomServiceDep,
    body: RoomCreate,
    user: User = Depends(require_permission(P.ROOM_CREATE)),
) -> Any:
    try:
        room = await service.create(user, body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _room_public(room, service.seats(room.id))


@router.get(
    "/",
    response_model=RoomsPublic,
    dependencies=[Depends(require_permission(P.ROOM_VIEW_PUBLIC))],
)
def list_rooms(*, service: RoomServiceDep, skip: int = 0, limit: int = 100) -> Any:
    rooms, count = service.list_public(limit=limit, offset=skip)
    return RoomsPublic(
        data=[_room_public(r, service.seats(r.id), privileged=False) for r in rooms],
        count=count,
    )


@router.get("/{room_id}", response_model=RoomPublic)
def get_room(
    *,
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    room_id: uuid.UUID,
) -> Any:
    try:
        room = service.get(room_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    seats = service.seats(room_id)
    perms = current_permissions(request, session, user, room_id)
    seated = any(s.user_id == user.id for s in seats)
    privileged = P.ROOM_VIEW_SEAT_DETAIL in perms or room.creator_id == user.id
    allowed = (
        privileged
        or seated
        or room.visibility == "unlisted"  # "viewable with the link" (DECISIONS §1.9)
        or (room.visibility == "public" and P.ROOM_VIEW_PUBLIC in perms)
    )
    if not allowed:
        # Never distinguish "private" from "does not exist": both 404.
        raise HTTPException(status_code=404, detail="Room not found")
    return _room_public(room, seats, privileged=privileged, viewer_id=user.id)


@router.post(
    "/join",
    response_model=RoomSeatJoinPublic,
    dependencies=[
        Depends(rate_limit_from_setting("room:join", settings.RATE_LIMIT_ROOM_JOIN))
    ],
)
async def join_room(
    *,
    service: RoomServiceDep,
    queue: JoinQueueDep,
    body: RoomJoinRequest,
    user: User = Depends(require_permission(P.ROOM_JOIN)),
) -> Any:
    try:
        seat = await service.join_by_code(user, body.code)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    except RoomNotWaitingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RoomFullError as exc:
        queue_status = await queue.snapshot(f"room:{exc.room_id}", str(user.id))
        return JSONResponse(
            {
                "detail": str(exc),
                "room_id": str(exc.room_id),
                "queue": queue_status.model_dump(),
            },
            status_code=409,
        )
    return RoomSeatJoinPublic(
        **_seat_public(seat, reveal=True).model_dump(), room_id=seat.room_id
    )


@router.post("/{room_id}/leave", response_model=Message)
async def leave_room(
    *, service: RoomServiceDep, user: CurrentUser, room_id: uuid.UUID
) -> Any:
    try:
        await service.leave(user, room_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    return Message(message="left")


@router.post("/{room_id}/start", response_model=RoomPublic)
async def start_room(
    *,
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    room_id: uuid.UUID,
) -> Any:
    try:
        room = service.get(room_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    perms = current_permissions(request, session, user, room_id)
    if not _may_operate(room, user, perms):
        raise HTTPException(
            status_code=403, detail=f"missing permission: {P.ROOM_FORCE_STOP}"
        )
    try:
        room = await service.start(room_id)
    except (RoomNotWaitingError, RoomFullError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _room_public(room, service.seats(room_id))


@router.post("/{room_id}/force-start", response_model=RoomPublic)
async def force_start_room(
    *,
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    room_id: uuid.UUID,
) -> Any:
    try:
        room = service.get(room_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    perms = current_permissions(request, session, user, room_id)
    if not _may_operate(room, user, perms):
        raise HTTPException(
            status_code=403, detail=f"missing permission: {P.ROOM_FORCE_STOP}"
        )
    try:
        room = await service.force_start(user, room_id)
    except (RoomNotWaitingError, RoomFullError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _room_public(room, service.seats(room_id))


@router.post("/{room_id}/stop", response_model=RoomPublic)
async def stop_room(
    *,
    service: RoomServiceDep,
    room_id: uuid.UUID,
    _user: User = Depends(
        require_permission(P.ROOM_FORCE_STOP, room_id_param="room_id")
    ),
) -> Any:
    try:
        room = await service.stop(room_id, reason="stopped by moderator")
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    return _room_public(room, service.seats(room_id))


@router.get("/{room_id}/queue", response_model=QueueStatus)
async def room_queue(
    *,
    service: RoomServiceDep,
    queue: JoinQueueDep,
    user: CurrentUser,
    room_id: uuid.UUID,
    scope: Literal["seat", "global"] = "seat",
) -> Any:
    # "seat" (default): is *this user* waiting for (or offered) a seat in
    # this room. "global" (DECISIONS.md §2's concurrent-game cap): is *this
    # room itself* waiting for a slot to start in -- same JoinQueue, same
    # QueueStatus shape, just keyed by room id instead of user id.
    #
    # There is no standalone sweeper task (docs/capacity-and-queueing.md's
    # "Left for later" -- reclaim happens opportunistically instead): a
    # `join_by_code` retry sweeps stale offers as a side effect, and so does
    # every poll of this route while the waiting party's own queue UI is
    # open. A seat offer with nobody polling and nobody else ever retrying
    # that exact room would stay stale until one of those two things
    # happens -- a real, bounded gap, not a silent one.
    if scope == "seat":
        await service._reclaim_expired_offers(room_id)
    key, member = (
        ("global", str(room_id))
        if scope == "global"
        else (
            f"room:{room_id}",
            str(user.id),
        )
    )
    queue_status = await queue.snapshot(key, member)
    if queue_status.position is None and not queue_status.offered:
        raise HTTPException(status_code=404, detail="not queued")
    return queue_status


# -- live play (Phase 8: the minimal API a human plays through) -------------
#
# GET /events (HTTP polling) and /ws (push, Phase 10) are two transports over
# the identical subscription_guard/filter_for_subscriber pipeline -- neither
# one's visibility can diverge from the other's, since both call the exact
# same functions. A terminal or script client can still poll GET /events
# with the cursor it last saw; a browser client uses /ws for a live table.
# Either way: read GET /view to know whose turn it is and what it may do,
# and post to POST /moves when it is theirs. subscription_guard
# (app/rooms/guard.py, written and table-tested in Phase 7) is what both
# transports gate on.


@router.get("/{room_id}/view")
def room_view(*, service: RoomServiceDep, user: CurrentUser, room_id: uuid.UUID) -> Any:
    """The caller's own current ``PlayerView`` of the live game. Always the
    live ``GameState`` under ``GameRunner``, never the database (which holds
    only room/seat metadata, not game state)."""
    try:
        return service.live_view(user, room_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    except NotSeatedError:
        raise HTTPException(status_code=403, detail="not seated in this room")
    except RoomNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


_MAX_MOVE_BYTES = 4096  # generous for any legal move dict (card index, color, ...)


class MoveSubmission(BaseModel):
    move: dict[str, Any]

    @field_validator("move")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > _MAX_MOVE_BYTES:
            msg = f"move body exceeds {_MAX_MOVE_BYTES} bytes"
            raise ValueError(msg)
        return value


@router.post(
    "/{room_id}/moves",
    response_model=Message,
    dependencies=[
        Depends(rate_limit_from_setting("room:move", settings.RATE_LIMIT_ROOM_MOVE))
    ],
)
def submit_move(
    *,
    service: RoomServiceDep,
    user: CurrentUser,
    room_id: uuid.UUID,
    body: MoveSubmission,
) -> Any:
    """Forwards the raw move dict to the caller's own seat. The referee (not
    this route) validates it via the game's ``parse_move``/``apply`` and
    reports a rejection as a seat-visible ``game.move_rejected`` event on
    ``GET /events`` -- poll that to find out whether it was accepted."""
    try:
        service.submit_move(user, room_id, body.move)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    except NotSeatedError:
        raise HTTPException(status_code=403, detail="not seated in this room")
    except RoomNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except NotYourTurnError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Message(message="submitted")


class ChatSend(BaseModel):
    text: str


@router.post(
    "/{room_id}/chat",
    response_model=Message,
    dependencies=[
        Depends(rate_limit_from_setting("room:chat", settings.RATE_LIMIT_CHAT))
    ],
)
async def send_chat(
    *,
    service: RoomServiceDep,
    user: CurrentUser,
    room_id: uuid.UUID,
    body: ChatSend,
) -> Any:
    """Publishes a public ``chat.message`` from the caller's own seat
    (DECISIONS.md §8) -- own-seat-only, matching ``/view``/``/moves``, and
    unlike those, works whether or not a game is currently running: chat
    never touches game state (docs/rooms.md)."""
    try:
        await service.send_chat(user, room_id, body.text)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found")
    except NotSeatedError:
        raise HTTPException(status_code=403, detail="not seated in this room")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return Message(message="sent")


@router.get("/{room_id}/moves/schema")
def moves_schema(
    *,
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    room_id: uuid.UUID,
) -> Any:
    """The game's move JSON Schema, for a terminal client building a valid
    ``POST /moves`` body. Gated identically to ``GET /events``/``GET
    /{room_id}`` -- it reveals nothing beyond the game type those already
    show."""
    resolve_subscriber(service, session, request, user, room_id)
    room = service.get(room_id)
    return move_json_schema(get_game(room.game))


@router.get("/{room_id}/events")
async def room_events(
    *,
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    room_id: uuid.UUID,
    since: Annotated[str | None, Query(pattern=r"^\d+(-\d+)?$")] = None,
    limit: int = 200,
) -> Any:
    """Poll the room's event stream from ``since`` (an event's own
    ``stream_id``; omit for full history). Filtered per
    ``app.events.filter_for_subscriber`` for the ``Subscriber``
    ``subscription_guard`` resolves for this caller -- a player sees public
    events plus their own seat's; nobody sees another seat's without
    ``room:view_seat_detail``."""
    subscriber = resolve_subscriber(service, session, request, user, room_id)
    events = await service.bus.history(str(room_id), since=since, limit=limit)
    visible = [e for e in events if filter_for_subscriber(e, subscriber) is not None]
    return {
        "events": [e.model_dump(mode="json") for e in visible],
        "next_since": events[-1].stream_id if events else since,
    }


@router.get("/{room_id}/ws-ticket")
def ws_ticket(*, user: CurrentUser, room_id: uuid.UUID) -> dict[str, str]:  # noqa: ARG001
    """A short-lived (30s) token for ``WS /{room_id}/ws``. Browsers cannot
    set a websocket's ``Authorization`` header, so the real, long-lived
    ``access_token`` would otherwise have to travel as a URL query
    parameter -- a much wider log-exposure surface (proxy/server access
    logs, browser history) than a header ever gets, for the socket's
    entire connection lifetime. This ticket is only ever useful for the
    ~30s between issuing it and opening the socket; ``room_id`` isn't
    actually checked here (real admission is rechecked by ``room_ws``
    itself at connect time), it's only in the URL for symmetry with
    ``/ws`` next to it."""
    ticket = security.create_access_token(user.id, timedelta(seconds=30))
    return {"ticket": ticket}


def _current_user_ws(session: Session, token: str | None) -> User | None:
    """Same JWT check as ``app.api.deps.get_current_user``, adapted for a
    websocket connection: a browser's WebSocket API cannot set an
    ``Authorization`` header, so the token travels as a query parameter
    (``?token=...``) instead. Returns ``None`` rather than raising, so the
    caller closes the socket with one uniform code regardless of *why* the
    connection is refused (see ``room_ws`` below) -- a bad/missing token
    must not be distinguishable from a denied subscription."""
    if token is None:
        return None
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except InvalidTokenError, ValidationError:
        return None
    user = session.get(User, token_data.sub)
    if user is None or not user.is_active:
        return None
    return user


def _resolve_subscriber_ws(
    service: RoomService, session: Session, user: User, room_id: uuid.UUID
) -> Subscriber | None:
    """The same ``subscription_guard`` ``GET /events`` uses, resolved once
    per connect instead of once per poll (docs/rooms.md's "left for later"
    note) -- the guard's own logic is untouched. Calls ``load_permissions``
    directly rather than going through ``current_permissions``'s
    per-HTTP-request cache, which has nothing to share across for a single
    one-shot check on connect."""
    try:
        room = service.get(room_id)
    except LookupError:
        return None
    perms = load_permissions(session, user.id, room_id)
    return subscription_guard(room, service.seats(room_id), user, perms)


@router.websocket("/{room_id}/ws")
async def room_ws(
    *,
    websocket: WebSocket,
    session: SessionDep,
    room_id: uuid.UUID,
    token: str | None = Query(default=None),
    since: Annotated[str | None, Query(pattern=r"^\d+(-\d+)?$")] = None,
) -> None:
    """Push transport for ``GET /events`` -- the real-time transport
    ``docs/rooms.md`` left for Phase 10; the polling route stays as a
    working fallback for any client that doesn't need push. Same admission
    (``subscription_guard``) and filtering (``filter_for_subscriber``)
    pipeline as the polling route, so visibility can never diverge between
    the two transports. Every refusal (bad/missing token, unknown room,
    denied subscription) closes with the identical code, matching the REST
    layer's "never leak existence" 404-either-way rule -- a caller cannot
    tell "wrong token" from "no such room" from "not invited" apart.

    ``since`` resumes a dropped connection exactly like the polling route's
    own query param: omit for live-only, ``"0"`` to replay everything
    retained first, or a previously-seen ``stream_id`` to resume after it.
    """
    # A connection attempt is cheap for a client but costs this handler a
    # JWT decode plus (for a real room id) a service.get()/load_permissions()
    # round-trip before ever refusing -- every other room route that does
    # real work is rate limited (rate_limit_from_setting), but that
    # dependency is coupled to CurrentUser's HTTP-only Authorization-header
    # flow, which this route deliberately doesn't use (see _current_user_ws).
    # Rate limit by client host instead, before touching the token at all.
    client_host = websocket.client.host if websocket.client else "unknown"
    allowed, _retry_after = await check_rate(
        get_redis(), f"rl:room:ws:{client_host}", 20, 60
    )
    if not allowed:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user = _current_user_ws(session, token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    service = RoomService(
        session,
        websocket.app.state.runner,
        JoinQueue(get_redis()),
        websocket.app.state.model_registry,
        websocket.app.state.bus,
    )
    subscriber = _resolve_subscriber_ws(service, session, user, room_id)
    if subscriber is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    ACTIVE_WS_CONNECTIONS.inc()
    try:
        with wide_event(
            "ws.session",
            room_id=str(room_id),
            user_id=str(user.id),
            subscriber_kind=subscriber.kind,
            seat=subscriber.seat,
        ) as ev:
            try:
                async for event in service.bus.subscribe(str(room_id), since=since):
                    filtered = filter_for_subscriber(event, subscriber)
                    if filtered is not None:
                        await websocket.send_json(filtered.model_dump(mode="json"))
                        ev.incr("events_sent")
            except WebSocketDisconnect:
                ev.set(close_reason="client_disconnect")
    finally:
        ACTIVE_WS_CONNECTIONS.dec()
