"""Leaderboard aggregation (Phase 12, DECISIONS.md §11): "leaderboard per
human account and per model, with aggregate admin views."

``app.leaderboard`` joins ``game_record`` to ``room_seat`` on ``room_id`` --
a room plays exactly one game for its whole lifetime (``RoomService.start``
refuses anything but ``"waiting"``, and nothing ever reopens a room), so
``room_seat`` already is that game's durable participant record. These
tests build real ``Room``/``RoomSeat`` rows directly (no need for
``RoomService``/Redis/a real game run -- that path is already proven by
``tests/rooms/test_game_history.py``) and write ``GameRecord`` rows
directly too, to test the aggregation query in isolation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz import service as authz
from app.core.config import settings
from app.leaderboard import leaderboard_by_account, leaderboard_by_model
from app.models import GameRecord, Room, RoomSeat, User
from tests.utils.user import create_random_user, user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR


def _room(db: Session, creator_id: uuid.UUID, game: str = "uno_classic") -> Room:
    room = Room(
        code=uuid.uuid4().hex[:8],
        game=game,
        seat_count=2,
        status="finished",
        visibility="private",
        creator_id=creator_id,
        seed=1,
    )
    db.add(room)
    db.flush()
    return room


def _human_seat(db: Session, room_id: uuid.UUID, seat: int, user_id: uuid.UUID) -> None:
    db.add(
        RoomSeat(
            room_id=room_id, seat=seat, kind="human", user_id=user_id, status="filled"
        )
    )


def _agent_seat(db: Session, room_id: uuid.UUID, seat: int, model: str) -> None:
    db.add(
        RoomSeat(
            room_id=room_id,
            seat=seat,
            kind="agent",
            model=model,
            persona="steady",
            status="filled",
        )
    )


def _record(
    room_id: uuid.UUID,
    *,
    game: str = "uno_classic",
    outcome: str = "finished",
    winner_seat: int | None = 0,
) -> GameRecord:
    now = datetime.now(UTC)
    finished = outcome == "finished"
    return GameRecord(
        id=uuid.uuid4().hex,
        room_id=room_id,
        game=game,
        seat_count=2,
        seed=1,
        outcome=outcome,
        winner_seat=winner_seat if finished else None,
        ranking=[winner_seat, 1 - winner_seat] if finished else [],
        scores=[0, 0] if finished else [],
        move_count=1,
        started_at=now,
        finished_at=now,
    )


def test_leaderboard_by_account_counts_wins_and_games_played_across_rooms(
    db: Session,
) -> None:
    creator = create_random_user(db)
    a, b = create_random_user(db), create_random_user(db)

    room1 = _room(db, creator.id)
    _human_seat(db, room1.id, 0, a.id)
    _human_seat(db, room1.id, 1, b.id)
    db.add(_record(room1.id, winner_seat=0))  # a wins

    room2 = _room(db, creator.id)
    _human_seat(db, room2.id, 0, b.id)
    _human_seat(db, room2.id, 1, a.id)
    db.add(_record(room2.id, winner_seat=0))  # b wins, a loses
    db.commit()

    entries = {e.subject_id: e for e in leaderboard_by_account(db)}
    assert entries[str(a.id)].games_played == 2
    assert entries[str(a.id)].wins == 1
    assert entries[str(a.id)].win_rate == 0.5
    assert entries[str(b.id)].games_played == 2
    assert entries[str(b.id)].wins == 1


def test_stopped_games_have_no_winner_and_are_excluded(db: Session) -> None:
    creator = create_random_user(db)
    a, b = create_random_user(db), create_random_user(db)

    room = _room(db, creator.id)
    _human_seat(db, room.id, 0, a.id)
    _human_seat(db, room.id, 1, b.id)
    db.add(_record(room.id, outcome="stopped", winner_seat=None))
    db.commit()

    subject_ids = {e.subject_id for e in leaderboard_by_account(db)}
    assert str(a.id) not in subject_ids
    assert str(b.id) not in subject_ids


def test_leaderboard_by_model_groups_by_model_not_persona(db: Session) -> None:
    creator = create_random_user(db)
    a = create_random_user(db)

    room1 = _room(db, creator.id)
    _agent_seat(db, room1.id, 0, "model-x")
    _agent_seat(db, room1.id, 1, "model-y")
    db.add(_record(room1.id, winner_seat=0))  # model-x wins

    room2 = _room(db, creator.id)
    _agent_seat(db, room2.id, 0, "model-x")
    _human_seat(db, room2.id, 1, a.id)
    db.add(_record(room2.id, winner_seat=1))  # a wins, model-x loses
    db.commit()

    entries = {e.subject_id: e for e in leaderboard_by_model(db)}
    assert entries["model-x"].games_played == 2
    assert entries["model-x"].wins == 1
    assert entries["model-y"].games_played == 1
    assert entries["model-y"].wins == 0


def test_game_filter_scopes_to_one_game_type(db: Session) -> None:
    creator = create_random_user(db)
    a, b = create_random_user(db), create_random_user(db)

    uno_room = _room(db, creator.id, game="uno_classic")
    _human_seat(db, uno_room.id, 0, a.id)
    _human_seat(db, uno_room.id, 1, b.id)
    db.add(_record(uno_room.id, game="uno_classic", winner_seat=0))

    hearts_room = _room(db, creator.id, game="hearts")
    _human_seat(db, hearts_room.id, 0, a.id)
    _human_seat(db, hearts_room.id, 1, b.id)
    db.add(_record(hearts_room.id, game="hearts", winner_seat=1))
    db.commit()

    hearts_entries = {e.subject_id: e for e in leaderboard_by_account(db, "hearts")}
    assert hearts_entries[str(a.id)].games_played == 1
    assert hearts_entries[str(a.id)].wins == 0
    assert hearts_entries[str(b.id)].games_played == 1
    assert hearts_entries[str(b.id)].wins == 1

    all_entries = {e.subject_id: e for e in leaderboard_by_account(db)}
    assert all_entries[str(a.id)].games_played == 2
    assert all_entries[str(a.id)].wins == 1


def _new_user_headers(client: TestClient) -> dict[str, str]:
    email, password = random_email(), random_lower_string()
    r = client.post(f"{API}/users/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return user_authentication_headers(client=client, email=email, password=password)


def test_get_leaderboard_accounts_http_200(client: TestClient) -> None:
    headers = _new_user_headers(client)
    r = client.get(f"{API}/leaderboard/accounts", headers=headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_get_leaderboard_models_http_200(client: TestClient) -> None:
    headers = _new_user_headers(client)
    r = client.get(f"{API}/leaderboard/models", headers=headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_leaderboard_requires_leaderboard_view_permission(
    client: TestClient, db: Session, normal_user_token_headers: dict[str, str]
) -> None:
    user = db.exec(select(User).where(User.email == settings.EMAIL_TEST_USER)).one()
    authz.revoke_role(db, user.id, "player")
    try:
        r = client.get(f"{API}/leaderboard/accounts", headers=normal_user_token_headers)
        assert r.status_code == 403
    finally:
        authz.grant_role(db, user.id, "player")
