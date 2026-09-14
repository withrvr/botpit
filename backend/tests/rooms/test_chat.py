"""HTTP-level tests for ``POST /rooms/{room_id}/chat`` (Phase 11,
DECISIONS.md §8). Uses the real app (a real ``GameRunner``) so real human
seats send real chat through the HTTP surface, not mocks.
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


def _create_and_join(client: TestClient, creator_headers: dict[str, str]) -> LiveGame:
    """Creates a private 2-human-seat room and joins two fresh users to
    fill both seats (the creator is never auto-seated -- RoomService.create
    only seats a human whose user_id was given, which this doesn't).
    Deliberately does *not* start it, so tests can check chat works before
    a game exists too."""
    r = client.post(f"{API}/rooms/", headers=creator_headers, json=_body())
    assert r.status_code == 200, r.text
    room_id, code = r.json()["id"], r.json()["code"]

    seats: dict[int, dict[str, str]] = {}
    for _ in range(2):
        headers = _new_user_headers(client)
        joined = client.post(f"{API}/rooms/join", headers=headers, json={"code": code})
        assert joined.status_code == 200, joined.text
        seats[joined.json()["seat"]] = headers
    return room_id, seats


@pytest.fixture
def waiting_room(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> Iterator[LiveGame]:
    room_id, seats = _create_and_join(client, superuser_token_headers)
    yield room_id, seats
    client.post(f"{API}/rooms/{room_id}/stop", headers=superuser_token_headers)


@pytest.fixture
def live_game(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> Iterator[LiveGame]:
    room_id, seats = _create_and_join(client, superuser_token_headers)
    r = client.post(f"{API}/rooms/{room_id}/start", headers=superuser_token_headers)
    assert r.status_code == 200, r.text
    yield room_id, seats
    client.post(f"{API}/rooms/{room_id}/stop", headers=superuser_token_headers)


def _poll_events(
    client: TestClient, room_id: str, headers: dict[str, str], predicate: Any
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(60):
        r = client.get(f"{API}/rooms/{room_id}/events", headers=headers)
        assert r.status_code == 200, r.text
        events = r.json()["events"]
        if predicate(events):
            return events
        time.sleep(0.05)
    return events


def test_chat_send_is_visible_via_events(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, seats = live_game
    r = client.post(
        f"{API}/rooms/{room_id}/chat", headers=seats[0], json={"text": "gl hf"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "sent"

    events = _poll_events(
        client,
        room_id,
        seats[1],
        lambda evs: any(e["type"] == "chat.message" for e in evs),
    )
    chats = [e for e in events if e["type"] == "chat.message"]
    assert any(
        c["payload"]["seat"] == 0 and c["payload"]["text"] == "gl hf" for c in chats
    )


def test_chat_works_before_the_game_starts(
    client: TestClient, waiting_room: LiveGame
) -> None:
    """Chat never touches game state -- unlike /moves, it must not require
    a running game."""
    room_id, seats = waiting_room
    r = client.post(
        f"{API}/rooms/{room_id}/chat", headers=seats[0], json={"text": "hello"}
    )
    assert r.status_code == 200, r.text


def test_chat_403s_for_an_outsider(client: TestClient, live_game: LiveGame) -> None:
    room_id, _seats = live_game
    outsider = _new_user_headers(client)
    r = client.post(
        f"{API}/rooms/{room_id}/chat", headers=outsider, json={"text": "hi"}
    )
    assert r.status_code == 403


def test_chat_422s_for_empty_text(client: TestClient, live_game: LiveGame) -> None:
    room_id, seats = live_game
    r = client.post(
        f"{API}/rooms/{room_id}/chat", headers=seats[0], json={"text": "   "}
    )
    assert r.status_code == 422


def test_chat_422s_for_text_over_200_chars(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, seats = live_game
    r = client.post(
        f"{API}/rooms/{room_id}/chat", headers=seats[0], json={"text": "x" * 201}
    )
    assert r.status_code == 422


def test_chat_is_rate_limited(client: TestClient, live_game: LiveGame) -> None:
    """Review precedent: every mutation route (create, join, move) is rate
    limited; chat should be too (RATE_LIMIT_CHAT, default 10/10)."""
    room_id, seats = live_game
    statuses = []
    for i in range(30):
        r = client.post(
            f"{API}/rooms/{room_id}/chat", headers=seats[0], json={"text": f"msg {i}"}
        )
        statuses.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in statuses
