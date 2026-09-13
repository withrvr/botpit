"""Durable game history (Phase 9, docs/games.md): what survives after a room's
live game ends. Everything here reads from Postgres (`GameRecord`/`GameEvent`,
written by `RoomService._archive_game`), never the live `EventBus` -- that is
Phase 8's `/rooms/{room_id}/events`'s job while a game is still running.

Every route resolves admission through the identical
`app.rooms.guard.resolve_subscriber` Phase 8's live routes use, so a finished
game's privacy rules never diverge from the room's own -- a private room
stays private after its game ends, and another seat's hand stays hidden.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import CurrentUser, RoomServiceDep, SessionDep
from app.api.views import view_payload
from app.events import EventEnvelope, Subscriber, filter_for_subscriber
from app.games import get_game
from app.models import GameEvent, GameRecord, GameRecordPublic
from app.referee import Referee, ReplayMismatchError
from app.rooms.guard import resolve_subscriber
from app.telemetry import get_logger

log = get_logger("games")

router = APIRouter(prefix="/games", tags=["games"])


def _record_public(record: Any) -> GameRecordPublic:
    return GameRecordPublic(
        id=record.id,
        room_id=record.room_id,
        game=record.game,
        seat_count=record.seat_count,
        outcome=record.outcome,
        winner_seat=record.winner_seat,
        ranking=record.ranking,
        scores=record.scores,
        move_count=record.move_count,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _admitted_record(
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    game_id: str,
) -> tuple[GameRecord, Subscriber]:
    """Looks up the game and checks room admission as one indistinguishable
    404: an unknown ``game_id`` and a real game in a room the caller can't
    see must read identically, matching ``get_room``'s own "never
    distinguish 'does not exist' from 'denied'" rule -- a caller who already
    holds some ``game_id`` (a former player, a leaked log line) must not be
    able to tell "no such game" apart from "that game's room denies you"."""
    try:
        record = service.get_game_record(game_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Game not found") from None
    try:
        subscriber = resolve_subscriber(service, session, request, user, record.room_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Game not found") from None
    return record, subscriber


def _to_envelope(e: GameEvent) -> EventEnvelope:
    return EventEnvelope.model_validate(
        {
            "event_id": str(e.id),
            "room_id": str(e.room_id),
            "seq": e.seq,
            "type": e.type,
            "ts": e.ts,
            "visibility": e.visibility,
            "payload": e.payload,
            "correlation": e.correlation,
            "stream_id": e.stream_id,
        }
    )


@router.get("/{game_id}", response_model=GameRecordPublic)
def get_game_record(
    *,
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    game_id: str,
) -> Any:
    record, _subscriber = _admitted_record(service, session, request, user, game_id)
    return _record_public(record)


@router.get("/{game_id}/events")
def game_events(
    *,
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    game_id: str,
    since: int | None = None,
    limit: int = 200,
) -> Any:
    """The durable counterpart to the live ``/rooms/{room_id}/events``:
    ``since``/``next_since`` here are the archived ``seq`` integer, not a
    live bus cursor -- the game is over, there is nothing left to poll for."""
    record, subscriber = _admitted_record(service, session, request, user, game_id)
    rows = service.game_events(record.id, since=since, limit=limit)
    envelopes = [_to_envelope(r) for r in rows]
    visible = [e for e in envelopes if filter_for_subscriber(e, subscriber) is not None]
    return {
        "events": [e.model_dump(mode="json") for e in visible],
        "next_since": rows[-1].seq if rows else since,
    }


@router.get("/{game_id}/replay")
def replay(
    *,
    service: RoomServiceDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    game_id: str,
    seat: Annotated[int | None, Query(ge=0)] = None,
    upto: Annotated[int | None, Query(ge=0)] = None,
) -> Any:
    """Rebuilds one seat's ``PlayerView`` at any point in a finished game's
    history, straight from the archived move log (DECISIONS.md §12: "the
    event log is the source of truth; finished games replay from it") --
    ``upto`` (default: every move) truncates the replay to that many moves.

    Privacy stays exactly what it was live: a non-privileged caller may only
    ever request their own seat; a privileged caller (creator or
    ``room:view_seat_detail``) may request any seat, one at a time, same as
    the room's own live ``/view`` -- a cross-seat admin view is Phase 10's
    job, not this route's.
    """
    record, subscriber = _admitted_record(service, session, request, user, game_id)

    if seat is None:
        seat = subscriber.seat
        if seat is None:
            raise HTTPException(
                status_code=400, detail="seat is required (you hold no seat here)"
            )
    elif subscriber.kind != "privileged" and seat != subscriber.seat:
        raise HTTPException(status_code=403, detail="not your seat")

    rows = service.game_events(record.id, since=None, limit=100_000)
    moves = [r.payload["move"] for r in rows if r.type == "game.move_applied"]
    if upto is None:
        upto = len(moves)
    elif upto > len(moves):
        raise HTTPException(
            status_code=422, detail=f"upto exceeds this game's {len(moves)} moves"
        )

    try:
        state = Referee.replay(
            record.game, record.seed, record.seat_count, moves[:upto]
        )
    except ReplayMismatchError:
        log.exception("archived move log for game %s failed to replay", game_id)
        raise HTTPException(
            status_code=500, detail="archived history failed to replay"
        ) from None

    game = get_game(record.game)
    return view_payload(game, state, seat)
