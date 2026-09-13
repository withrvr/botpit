"""HTTP-level tests for the Phase 8 live-play routes (docs/rooms.md):
``/view``, ``/moves``, ``/moves/schema``, ``/events``. Uses the real app (a
real ``GameRunner`` from the lifespan) so two real human seats play a real,
seeded ``uno_classic`` game through the HTTP surface, not mocks.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

pytestmark = pytest.mark.redis

API = settings.API_V1_STR

LiveGame = tuple[str, dict[int, dict[str, str]]]


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


def _start_two_seat_game(
    client: TestClient, creator_headers: dict[str, str]
) -> LiveGame:
    """Creates a private 2-human-seat room, joins two fresh users to fill it,
    and starts it. Returns ``(room_id, {seat_number: headers})``."""
    r = client.post(f"{API}/rooms/", headers=creator_headers, json=_body())
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
    assert r.json()["status"] == "in_progress"
    return room_id, seats


@pytest.fixture
def live_game(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> Iterator[LiveGame]:
    """Starts a real 2-human-seat game and stops it afterwards: without this,
    every started room stays ``in_progress`` and this file's own tests trip
    ``MAX_CONCURRENT_GAMES`` (default 4) after a handful of them."""
    room_id, seats = _start_two_seat_game(client, superuser_token_headers)
    yield room_id, seats
    client.post(f"{API}/rooms/{room_id}/stop", headers=superuser_token_headers)


def _poll_events(
    client: TestClient, room_id: str, headers: dict[str, str], predicate: Any
) -> list[dict[str, Any]]:
    """Polls ``/events`` (up to ~3s) until ``predicate(events)`` is true."""
    events: list[dict[str, Any]] = []
    for _ in range(60):
        r = client.get(f"{API}/rooms/{room_id}/events", headers=headers)
        assert r.status_code == 200, r.text
        events = r.json()["events"]
        if predicate(events):
            return events
        time.sleep(0.05)
    return events


# -- /view --------------------------------------------------------------


def test_view_403s_for_a_caller_with_no_seat(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, _seats = live_game
    outsider = _new_user_headers(client)
    r = client.get(f"{API}/rooms/{room_id}/view", headers=outsider)
    assert r.status_code == 403


def test_view_409s_when_the_room_has_no_running_game(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    r = client.post(f"{API}/rooms/", headers=h, json=_body())
    room_id, code = r.json()["id"], r.json()["code"]
    joiner = _new_user_headers(client)
    client.post(f"{API}/rooms/join", headers=joiner, json={"code": code})

    r = client.get(f"{API}/rooms/{room_id}/view", headers=joiner)
    assert r.status_code == 409


def test_view_shows_own_seat_and_legal_moves(
    client: TestClient, live_game: LiveGame
) -> None:
    """Every seat sees its own ``seat``/``hand``; only the seat whose turn it
    is has any ``legal_moves`` (the other's list is empty, not an error)."""
    room_id, seats = live_game
    views = {}
    for seat_no, headers in seats.items():
        r = client.get(f"{API}/rooms/{room_id}/view", headers=headers)
        assert r.status_code == 200, r.text
        view = r.json()
        assert view["seat"] == seat_no
        views[seat_no] = view

    current = views[0]["current_seat"]
    assert current == views[1]["current_seat"]
    assert len(views[current]["legal_moves"]) > 0
    assert views[1 - current]["legal_moves"] == []


# -- /moves ---------------------------------------------------------------


def test_moves_403s_for_a_caller_with_no_seat(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, _seats = live_game
    outsider = _new_user_headers(client)
    r = client.post(f"{API}/rooms/{room_id}/moves", headers=outsider, json={"move": {}})
    assert r.status_code == 403


def test_moves_409s_when_it_is_not_your_turn(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, seats = live_game
    view = client.get(f"{API}/rooms/{room_id}/view", headers=seats[0]).json()
    current = view["current_seat"]
    waiting_seat = 1 - current
    move = client.get(f"{API}/rooms/{room_id}/view", headers=seats[current]).json()[
        "legal_moves"
    ][0]

    r = client.post(
        f"{API}/rooms/{room_id}/moves", headers=seats[waiting_seat], json={"move": move}
    )
    assert r.status_code == 409


def test_submitted_move_is_applied_and_visible_via_events(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, seats = live_game
    view = client.get(f"{API}/rooms/{room_id}/view", headers=seats[0]).json()
    current = view["current_seat"]
    mover_headers = seats[current]
    move = client.get(f"{API}/rooms/{room_id}/view", headers=mover_headers).json()[
        "legal_moves"
    ][0]

    r = client.post(
        f"{API}/rooms/{room_id}/moves", headers=mover_headers, json={"move": move}
    )
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "submitted"

    events = _poll_events(
        client,
        room_id,
        mover_headers,
        lambda evs: any(
            e["type"] == "game.move_applied" and e["payload"]["seat"] == current
            for e in evs
        ),
    )
    applied = [e for e in events if e["type"] == "game.move_applied"]
    assert applied, "move never showed up as game.move_applied"
    assert applied[0]["payload"]["move"]["seat"] == current


# -- /moves/schema ----------------------------------------------------------


def test_moves_schema_404s_for_an_outsider_on_a_private_room(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, _seats = live_game
    outsider = _new_user_headers(client)
    r = client.get(f"{API}/rooms/{room_id}/moves/schema", headers=outsider)
    assert r.status_code == 404


def test_moves_schema_is_a_json_schema_seated_players_can_use(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, seats = live_game
    r = client.get(f"{API}/rooms/{room_id}/moves/schema", headers=seats[0])
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)
    assert r.json()  # non-empty


# -- /events ------------------------------------------------------------


def test_events_404s_for_an_outsider_on_a_private_room(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, _seats = live_game
    outsider = _new_user_headers(client)
    r = client.get(f"{API}/rooms/{room_id}/events", headers=outsider)
    assert r.status_code == 404


def test_events_never_leaks_another_seats_view_to_a_non_privileged_player(
    client: TestClient, live_game: LiveGame
) -> None:
    """Security boundary: ``game.view_updated`` carries a seat's full hand
    (``SeatVisibility``). A seated-but-not-privileged player must see their
    own view events and never the other seat's."""
    room_id, seats = live_game

    events = _poll_events(
        client,
        room_id,
        seats[0],
        lambda evs: any(e["type"] == "game.view_updated" for e in evs),
    )
    view_events = [e for e in events if e["type"] == "game.view_updated"]
    assert view_events, "seat 0 never saw its own view_updated"
    assert all(e["payload"]["seat"] == 0 for e in view_events)


def test_events_privileged_creator_sees_every_seats_view(
    client: TestClient, live_game: LiveGame, superuser_token_headers: dict[str, str]
) -> None:
    """The creator (privileged, per ``subscription_guard``) sees both seats'
    private view events -- proof ``filter_for_subscriber`` is wired to the
    real ``Subscriber`` the guard resolves, not a hardcoded "player"."""
    room_id, _seats = live_game
    _poll_events(
        client,
        room_id,
        superuser_token_headers,
        lambda evs: (
            {e["payload"]["seat"] for e in evs if e["type"] == "game.view_updated"}
            == {0, 1}
        ),
    )
    r = client.get(f"{API}/rooms/{room_id}/events", headers=superuser_token_headers)
    seats_seen = {
        e["payload"]["seat"]
        for e in r.json()["events"]
        if e["type"] == "game.view_updated"
    }
    assert seats_seen == {0, 1}
