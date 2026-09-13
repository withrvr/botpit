"""HTTP-level tests for /api/v1/rooms (docs/rooms.md): one auth-gate smoke
test per route plus a full happy path. Uses the real app (real ``GameRunner``
and ``ModelRegistry`` from the lifespan), so agent seats target whatever
``LLM_BASE_URL``/``LLM_MODELS`` this environment is configured with -- these
tests never wait on a real model call, only on the room/game *starting*.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import settings_store
from app.authz import service as authz
from app.core.config import settings
from app.models import User
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

pytestmark = pytest.mark.redis

API = settings.API_V1_STR


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """The superuser is reused (module-scoped token) across many room
    creates in this file; without this, ``RATE_LIMIT_ROOM_CREATE`` (5/60)
    starts rejecting later tests with a real 429 that has nothing to do
    with what each test is actually checking."""
    r = sync_redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    for key in r.scan_iter(match="rl:room:*"):
        r.delete(key)
    yield
    for key in r.scan_iter(match="rl:room:*"):
        r.delete(key)
    r.close()


def _body(
    game: str = "uno_classic",
    seat_count: int = 2,
    visibility: str = "private",
    agent: bool = False,
) -> dict[str, Any]:
    seat = {"kind": "agent"} if agent else {"kind": "human"}
    return {
        "game": game,
        "seat_count": seat_count,
        "visibility": visibility,
        "seats": [dict(seat) for _ in range(seat_count)],
    }


def _new_user_headers(client: TestClient) -> dict[str, str]:
    email, password = random_email(), random_lower_string()
    r = client.post(f"{API}/users/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return user_authentication_headers(client=client, email=email, password=password)


# -- auth gates -------------------------------------------------------------


def test_create_requires_room_create_permission(
    client: TestClient, db: Session, normal_user_token_headers: dict[str, str]
) -> None:
    user = db.exec(select(User).where(User.email == settings.EMAIL_TEST_USER)).one()
    authz.revoke_role(db, user.id, "player")
    try:
        r = client.post(
            f"{API}/rooms/", headers=normal_user_token_headers, json=_body()
        )
        assert r.status_code == 403
    finally:
        authz.grant_role(db, user.id, "player")


def test_create_bad_seat_count_is_422(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{API}/rooms/",
        headers=superuser_token_headers,
        json=_body(game="hearts", seat_count=2),
    )
    assert r.status_code == 422


def test_create_unknown_model_is_422(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    body = _body(seat_count=2, agent=True)
    body["seats"][0]["model"] = "no-such-model"
    r = client.post(f"{API}/rooms/", headers=superuser_token_headers, json=body)
    assert r.status_code == 422


def test_get_room_404s_for_unknown_id(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(f"{API}/rooms/{uuid.uuid4()}", headers=superuser_token_headers)
    assert r.status_code == 404


def test_get_private_room_404s_for_an_outsider(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    r = client.post(
        f"{API}/rooms/",
        headers=superuser_token_headers,
        json=_body(visibility="private"),
    )
    assert r.status_code == 200, r.text
    room_id = r.json()["id"]
    outsider = _new_user_headers(client)
    r = client.get(f"{API}/rooms/{room_id}", headers=outsider)
    assert r.status_code == 404


def test_join_unknown_code_is_404(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{API}/rooms/join", headers=superuser_token_headers, json={"code": "NOTREAL1"}
    )
    assert r.status_code == 404


def test_join_full_room_is_409_with_queue_body(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(f"{API}/rooms/", headers=superuser_token_headers, json=_body())
    code = r.json()["code"]
    for _ in range(2):
        headers = _new_user_headers(client)
        joined = client.post(f"{API}/rooms/join", headers=headers, json={"code": code})
        assert joined.status_code == 200, joined.text
    latecomer = _new_user_headers(client)
    r = client.post(f"{API}/rooms/join", headers=latecomer, json={"code": code})
    assert r.status_code == 409
    body = r.json()
    assert set(body["queue"]) == {"position", "length", "eta_seconds"}


def test_start_needs_creator_or_force_stop(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{API}/rooms/", headers=superuser_token_headers, json=_body(agent=True)
    )
    room_id = r.json()["id"]
    other = _new_user_headers(client)
    r = client.post(f"{API}/rooms/{room_id}/start", headers=other)
    assert r.status_code == 403
    r = client.post(f"{API}/rooms/{room_id}/stop", headers=superuser_token_headers)
    assert r.status_code == 200


def test_stop_requires_force_stop_permission(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{API}/rooms/", headers=superuser_token_headers, json=_body(agent=True)
    )
    room_id = r.json()["id"]
    other = _new_user_headers(client)
    r = client.post(f"{API}/rooms/{room_id}/stop", headers=other)
    assert r.status_code == 403
    r = client.post(f"{API}/rooms/{room_id}/stop", headers=superuser_token_headers)
    assert r.status_code == 200


def test_queue_position_404_when_not_queued(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(f"{API}/rooms/", headers=superuser_token_headers, json=_body())
    room_id = r.json()["id"]
    r = client.get(f"{API}/rooms/{room_id}/queue", headers=superuser_token_headers)
    assert r.status_code == 404


def test_capacity_exceeded_is_429_with_queue_body(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    h = superuser_token_headers
    settings_store.set_value(db, "max_concurrent_games", 1, None)
    room_id = None
    try:
        r1 = client.post(f"{API}/rooms/", headers=h, json=_body(agent=True))
        assert r1.status_code == 200, r1.text
        room_id = r1.json()["id"]
        r_start = client.post(f"{API}/rooms/{room_id}/start", headers=h)
        assert r_start.status_code == 200, r_start.text

        r2 = client.post(f"{API}/rooms/", headers=h, json=_body(agent=True))
        assert r2.status_code == 429
        assert set(r2.json()["queue"]) == {"position", "length", "eta_seconds"}
    finally:
        if room_id is not None:
            client.post(f"{API}/rooms/{room_id}/stop", headers=h)
        settings_store.set_value(
            db, "max_concurrent_games", settings.MAX_CONCURRENT_GAMES, None
        )


# -- happy path ---------------------------------------------------------


def test_room_happy_path(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    r = client.post(f"{API}/rooms/", headers=h, json=_body(visibility="private"))
    assert r.status_code == 200, r.text
    room = r.json()
    room_id, code = room["id"], room["code"]
    assert len(room["seats"]) == 2
    assert all(s["status"] == "open" for s in room["seats"])

    # a private room is never in the public listing
    r = client.get(f"{API}/rooms/", headers=h)
    assert r.status_code == 200
    assert all(x["id"] != room_id for x in r.json()["data"])

    # creator can see the full detail
    r = client.get(f"{API}/rooms/{room_id}", headers=h)
    assert r.status_code == 200

    joiner = _new_user_headers(client)
    r = client.post(f"{API}/rooms/join", headers=joiner, json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json()["seat"] == 0

    # a seated player can now see the room too
    r = client.get(f"{API}/rooms/{room_id}", headers=joiner)
    assert r.status_code == 200

    # one seat still open: start is refused
    r = client.post(f"{API}/rooms/{room_id}/start", headers=h)
    assert r.status_code == 409

    # force-start backfills the remaining seat and launches a real game
    r = client.post(f"{API}/rooms/{room_id}/force-start", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress"

    r = client.post(f"{API}/rooms/{room_id}/stop", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


def test_leave_frees_own_seat_only(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    r = client.post(f"{API}/rooms/", headers=h, json=_body())
    room_id, code = r.json()["id"], r.json()["code"]
    joiner = _new_user_headers(client)
    client.post(f"{API}/rooms/join", headers=joiner, json={"code": code})

    r = client.post(f"{API}/rooms/{room_id}/leave", headers=joiner)
    assert r.status_code == 200

    r = client.get(f"{API}/rooms/{room_id}", headers=h)
    seats = r.json()["seats"]
    assert next(s for s in seats if s["seat"] == 0)["status"] == "open"


def test_get_public_room_redacts_seat_identity_for_an_unseated_viewer(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """A public room is viewable by any ``room:view_public`` holder, but that
    must only widen *admission*, not reveal every seat's identity: an
    unseated spectator sees shape (kind/seat/status) but not who -- or which
    model/persona -- occupies a seat that isn't theirs."""
    h = superuser_token_headers
    r = client.post(
        f"{API}/rooms/", headers=h, json=_body(visibility="public", agent=True)
    )
    assert r.status_code == 200, r.text
    room_id = r.json()["id"]

    spectator = _new_user_headers(client)  # default "player" role: has room:view_public
    r = client.get(f"{API}/rooms/{room_id}", headers=spectator)
    assert r.status_code == 200
    for seat in r.json()["seats"]:
        assert seat["kind"] == "agent"
        assert seat["status"] == "filled"
        assert seat["model"] is None
        assert seat["persona"] is None
        assert seat["user_id"] is None

    # the creator still sees full detail of their own room
    r = client.get(f"{API}/rooms/{room_id}", headers=h)
    assert all(s["model"] is not None for s in r.json()["seats"])


def test_get_public_room_reveals_the_viewers_own_seat(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    r = client.post(
        f"{API}/rooms/", headers=h, json=_body(visibility="public", seat_count=2)
    )
    room_id, code = r.json()["id"], r.json()["code"]
    joiner = _new_user_headers(client)
    r = client.post(f"{API}/rooms/join", headers=joiner, json={"code": code})
    joiner_seat = r.json()["seat"]

    r = client.get(f"{API}/rooms/{room_id}", headers=joiner)
    seats = {s["seat"]: s for s in r.json()["seats"]}
    assert seats[joiner_seat]["user_id"] is not None  # their own seat: revealed
    other = next(s for seat_no, s in seats.items() if seat_no != joiner_seat)
    assert other["user_id"] is None  # someone else's: still redacted


def test_get_unlisted_room_is_viewable_by_any_authenticated_user(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """ "Viewable with the link, not listed publicly" (DECISIONS §1.9): unlike
    private, an unlisted room's id alone is the access control."""
    h = superuser_token_headers
    r = client.post(f"{API}/rooms/", headers=h, json=_body(visibility="unlisted"))
    room_id = r.json()["id"]

    outsider = _new_user_headers(client)  # not seated, not the creator
    r = client.get(f"{API}/rooms/{room_id}", headers=outsider)
    assert r.status_code == 200
    # still redacted for this unseated, unprivileged viewer
    assert all(s["user_id"] is None for s in r.json()["seats"])

    # and it never appears in the public listing (that's "not listed publicly")
    r = client.get(f"{API}/rooms/", headers=outsider)
    assert all(x["id"] != room_id for x in r.json()["data"])


def test_create_seat_user_id_for_another_user_is_422(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    victim = _new_user_headers(client)
    victim_id = client.get(f"{API}/users/me", headers=victim).json()["id"]
    body = _body(seat_count=2)
    body["seats"][0] = {"kind": "human", "user_id": victim_id}
    r = client.post(f"{API}/rooms/", headers=h, json=body)
    assert r.status_code == 422
