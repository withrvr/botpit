"""Phase 9: durable game history. ``RoomService._archive_game`` copies a
finished/stopped game's event log into ``game_record``/``game_event`` when
``GameRunner``'s completion watcher fires; ``app/api/routes/games.py`` reads
it back. Real app, real Postgres -- no mocks."""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.events import PUBLIC, EventBus, GameStarted, make_event
from app.models import GameEvent, GameRecord, Room, get_datetime_utc
from app.rooms.service import RoomService

pytestmark = pytest.mark.redis

API = settings.API_V1_STR


def _body(seat_count: int = 2, visibility: str = "private") -> dict[str, Any]:
    return {
        "game": "uno_classic",
        "seat_count": seat_count,
        "visibility": visibility,
        "seats": [{"kind": "human"} for _ in range(seat_count)],
    }


def _new_user_headers(client: TestClient) -> dict[str, str]:
    from tests.utils.user import user_authentication_headers
    from tests.utils.utils import random_email, random_lower_string

    email, password = random_email(), random_lower_string()
    r = client.post(f"{API}/users/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return user_authentication_headers(client=client, email=email, password=password)


def _start_and_stop(
    client: TestClient, creator_headers: dict[str, str], *, visibility: str = "private"
) -> tuple[str, dict[int, dict[str, str]]]:
    """Starts a real 2-human-seat game and immediately stops it -- the
    "stopped" archiving path, deterministic and fast (no moves needed)."""
    r = client.post(
        f"{API}/rooms/", headers=creator_headers, json=_body(visibility=visibility)
    )
    assert r.status_code == 200, r.text
    room_id, code = r.json()["id"], r.json()["code"]

    seats: dict[int, dict[str, str]] = {}
    for _ in range(2):
        headers = _new_user_headers(client)
        joined = client.post(f"{API}/rooms/join", headers=headers, json={"code": code})
        assert joined.status_code == 200, joined.text
        seats[joined.json()["seat"]] = headers

    r = client.post(f"{API}/rooms/{room_id}/start", headers=creator_headers)
    assert r.status_code == 200, r.text

    r = client.post(f"{API}/rooms/{room_id}/stop", headers=creator_headers)
    assert r.status_code == 200, r.text
    return room_id, seats


def _wait_for_record(db: Session, room_id: str) -> GameRecord:
    for _ in range(60):
        record = db.exec(
            select(GameRecord).where(GameRecord.room_id == room_id)
        ).first()
        if record is not None:
            return record
        db.expire_all()
        time.sleep(0.05)
    raise AssertionError(f"no GameRecord archived for room {room_id}")


def test_stopping_a_room_archives_a_game_record(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, _seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    assert record.game == "uno_classic"
    assert record.seat_count == 2
    assert record.outcome == "stopped"
    assert record.winner_seat is None
    assert record.ranking == []
    assert record.scores == []
    assert record.move_count == 0


def test_archived_events_exclude_room_lifecycle_noise(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """Only events carrying this game's ``game_id`` correlation get archived
    -- ``room.created``/``room.seat_filled`` (published by ``RoomService``,
    no game_id) already have their own row in ``room``/``room_seat`` and are
    excluded, unlike ``room.stopped`` (published by the *referee* itself on
    cancellation, so it does carry the correlation and belongs here)."""
    room_id, _seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    rows = db.exec(
        select(GameEvent).where(GameEvent.game_id == record.id).order_by(GameEvent.seq)
    ).all()
    assert rows, "no events archived"
    assert rows[0].type == "game.started"
    assert not any(r.type in ("room.created", "room.seat_filled") for r in rows)
    assert any(r.type == "room.stopped" for r in rows)
    # seqs are the bus's own monotonic order, preserved verbatim
    assert [r.seq for r in rows] == sorted(r.seq for r in rows)


def test_get_game_record_404s_for_an_outsider_on_a_private_room(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, _seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)
    outsider = _new_user_headers(client)

    r = client.get(f"{API}/games/{record.id}", headers=outsider)
    assert r.status_code == 404


def test_get_game_record_404_for_unknown_id(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(f"{API}/games/no-such-game", headers=superuser_token_headers)
    assert r.status_code == 404


def test_get_game_record_visible_to_creator_and_seated_player(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(f"{API}/games/{record.id}", headers=superuser_token_headers)
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "stopped"
    assert r.json()["room_id"] == room_id

    r = client.get(f"{API}/games/{record.id}", headers=seats[0])
    assert r.status_code == 200


def test_events_endpoint_hides_another_seats_view_for_a_non_privileged_player(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(f"{API}/games/{record.id}/events", headers=seats[0])
    assert r.status_code == 200, r.text
    view_events = [e for e in r.json()["events"] if e["type"] == "game.view_updated"]
    assert view_events, "seat 0 never archived its own view_updated"
    assert all(e["payload"]["seat"] == 0 for e in view_events)


def test_events_endpoint_shows_every_seat_to_the_creator(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, _seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(f"{API}/games/{record.id}/events", headers=superuser_token_headers)
    assert r.status_code == 200, r.text
    seats_seen = {
        e["payload"]["seat"]
        for e in r.json()["events"]
        if e["type"] == "game.view_updated"
    }
    assert seats_seen == {0, 1}


def test_events_pagination_via_next_since(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, _seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(
        f"{API}/games/{record.id}/events",
        headers=superuser_token_headers,
        params={"limit": 1},
    )
    assert r.status_code == 200, r.text
    first_page = r.json()
    assert len(first_page["events"]) == 1
    next_since = first_page["next_since"]
    assert next_since is not None

    r = client.get(
        f"{API}/games/{record.id}/events",
        headers=superuser_token_headers,
        params={"since": next_since},
    )
    assert r.status_code == 200, r.text
    second_page = r.json()
    assert all(e["seq"] > first_page["events"][0]["seq"] for e in second_page["events"])


def test_replay_defaults_to_the_callers_own_seat(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(f"{API}/games/{record.id}/replay", headers=seats[0])
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["seat"] == 0
    assert len(view["hand"]) > 0  # the initial deal, at zero moves played


def test_replay_refuses_another_seat_for_a_non_privileged_player(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(
        f"{API}/games/{record.id}/replay", headers=seats[0], params={"seat": 1}
    )
    assert r.status_code == 403


def test_replay_lets_the_creator_pick_any_seat(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, _seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(
        f"{API}/games/{record.id}/replay",
        headers=superuser_token_headers,
        params={"seat": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["seat"] == 1


def test_replay_requires_a_seat_when_the_caller_holds_none(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, _seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(f"{API}/games/{record.id}/replay", headers=superuser_token_headers)
    assert r.status_code == 400


async def test_finish_room_archives_even_when_stop_already_won_the_status_race(
    db: Session, service: RoomService, bus: EventBus
) -> None:
    """Regression: ``_finish_room`` used to nest archiving *inside* the
    "only write if still in_progress" guard, so whenever an explicit
    ``stop()`` happened to win its race with the completion watcher (both
    await the same cancelled task; either can resume first), archiving was
    silently skipped entirely -- despite the callback still firing exactly
    once, as it always does. Reproduced here deterministically: the room
    already reads "stopped" (simulating ``stop()`` having already won)
    *before* ``_finish_room`` is called at all."""
    room = Room(
        code="RACETEST01",
        game="uno_classic",
        seat_count=2,
        status="stopped",  # already written by a "winning" stop(), before _finish_room runs
        seed=1,
        started_at=get_datetime_utc(),
        finished_at=get_datetime_utc(),
    )
    db.add(room)
    db.commit()

    game_id = uuid.uuid4().hex
    event = make_event(
        str(room.id),
        GameStarted(game="uno_classic", seats=2, seed=1, seat_specs=[]),
        PUBLIC,
        correlation={"game_id": game_id},
    )
    await bus.publish(event)

    await service._finish_room(str(room.id), "stopped")

    record = service.get_game_record(game_id)  # raises LookupError if archiving was skipped
    assert record.outcome == "stopped"
    assert record.room_id == room.id


def test_replay_upto_beyond_move_count_is_422(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    room_id, seats = _start_and_stop(client, superuser_token_headers)
    record = _wait_for_record(db, room_id)

    r = client.get(
        f"{API}/games/{record.id}/replay", headers=seats[0], params={"upto": 999}
    )
    assert r.status_code == 422
