"""WebSocket-transport tests for ``/rooms/{room_id}/ws`` (Phase 10): the
same ``subscription_guard``/``filter_for_subscriber`` pipeline ``GET
/events`` uses, pushed instead of polled (docs/rooms.md). Uses the real
app (a real ``GameRunner``) exactly like ``test_rooms_live.py``, not mocks.
"""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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


def _token(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


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
    room_id, seats = _start_two_seat_game(client, superuser_token_headers)
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


def _recv_json(ws: Any, timeout: float = 5.0) -> dict[str, Any]:
    """``WebSocketTestSession.receive_json`` has no native timeout and
    blocks forever if nothing ever arrives -- wrap it so a real transport
    regression fails this one test instead of hanging the whole suite."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(ws.receive_json)
    try:
        return future.result(timeout=timeout)  # type: ignore[no-any-return]
    except concurrent.futures.TimeoutError:
        pytest.fail(f"no websocket message received within {timeout}s")
    finally:
        pool.shutdown(wait=False)


def _recv_until(
    ws: Any, predicate: Any, *, max_messages: int = 200, timeout: float = 5.0
) -> list[dict[str, Any]]:
    """Reads messages until ``predicate(seen)`` is true, returning everything
    seen so far (mirrors ``_poll_events``'s shape but over the push
    transport)."""
    seen: list[dict[str, Any]] = []
    for _ in range(max_messages):
        seen.append(_recv_json(ws, timeout=timeout))
        if predicate(seen):
            return seen
    return seen


# -- auth and admission ----------------------------------------------------


def test_ws_closes_with_1008_when_token_missing(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, _seats = live_game
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"{API}/rooms/{room_id}/ws"),
    ):
        pass
    assert exc_info.value.code == 1008


def test_ws_closes_with_1008_when_token_invalid(
    client: TestClient, live_game: LiveGame
) -> None:
    room_id, _seats = live_game
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"{API}/rooms/{room_id}/ws?token=not-a-real-jwt"),
    ):
        pass
    assert exc_info.value.code == 1008


def test_ws_closes_with_1008_for_outsider_on_private_room(
    client: TestClient, live_game: LiveGame
) -> None:
    """Denied subscription closes exactly like a bad token -- an outsider
    cannot distinguish "wrong token" from "not invited" from either close,
    matching the REST layer's 404-either-way rule (docs/rooms.md)."""
    room_id, _seats = live_game
    outsider = _token(_new_user_headers(client))
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"{API}/rooms/{room_id}/ws?token={outsider}"),
    ):
        pass
    assert exc_info.value.code == 1008


def test_ws_closes_with_1008_for_unknown_room(client: TestClient) -> None:
    import uuid

    from tests.utils.utils import get_superuser_token_headers

    token = _token(get_superuser_token_headers(client))
    fake_room_id = uuid.uuid4()
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"{API}/rooms/{fake_room_id}/ws?token={token}"),
    ):
        pass
    assert exc_info.value.code == 1008


# -- parity with GET /events ------------------------------------------------


def test_ws_replay_matches_rest_events_for_a_seated_player(
    client: TestClient, live_game: LiveGame
) -> None:
    """``since=0`` replays retained history then goes live -- for a snapshot
    taken right after replay, this must match ``GET /events`` byte-for-byte
    for the same subscriber, since both go through the identical
    ``filter_for_subscriber`` pipeline."""
    room_id, seats = live_game
    headers = seats[0]
    token = _token(headers)

    # Let the game produce a few real events before comparing snapshots.
    _poll_events(
        client,
        room_id,
        headers,
        lambda evs: any(e["type"] == "game.started" for e in evs),
    )

    rest_events = client.get(f"{API}/rooms/{room_id}/events", headers=headers).json()[
        "events"
    ]

    with client.websocket_connect(
        f"{API}/rooms/{room_id}/ws?token={token}&since=0-0"
    ) as ws:
        ws_events = _recv_until(
            ws, lambda seen: len(seen) >= len(rest_events), timeout=5.0
        )

    assert ws_events[: len(rest_events)] == rest_events


def test_ws_never_leaks_another_seats_view_to_a_non_privileged_player(
    client: TestClient, live_game: LiveGame
) -> None:
    """Security boundary, mirrored from the REST suite: a seated-but-not-
    privileged player must see their own ``game.view_updated`` and never
    the other seat's, over the push transport too."""
    room_id, seats = live_game
    token = _token(seats[0])

    with client.websocket_connect(
        f"{API}/rooms/{room_id}/ws?token={token}&since=0-0"
    ) as ws:
        events = _recv_until(
            ws,
            lambda seen: any(e["type"] == "game.view_updated" for e in seen),
            timeout=5.0,
        )

    view_events = [e for e in events if e["type"] == "game.view_updated"]
    assert view_events, "seat 0 never saw its own view_updated over ws"
    assert all(e["payload"]["seat"] == 0 for e in view_events)


def test_ws_privileged_creator_sees_every_seats_view(
    client: TestClient, live_game: LiveGame, superuser_token_headers: dict[str, str]
) -> None:
    """The privileged subscriber sees both seats' view events over the push
    transport too -- proof ``filter_for_subscriber`` is wired to the real
    ``Subscriber`` the guard resolves, not a hardcoded ``"player"``."""
    room_id, _seats = live_game
    token = _token(superuser_token_headers)

    with client.websocket_connect(
        f"{API}/rooms/{room_id}/ws?token={token}&since=0-0"
    ) as ws:
        events = _recv_until(
            ws,
            lambda seen: (
                {e["payload"]["seat"] for e in seen if e["type"] == "game.view_updated"}
                == {0, 1}
            ),
            timeout=5.0,
        )

    seats_seen = {
        e["payload"]["seat"] for e in events if e["type"] == "game.view_updated"
    }
    assert seats_seen == {0, 1}


# -- real push, not polling --------------------------------------------------


def test_ws_delivers_a_live_push_without_any_polling(
    client: TestClient, live_game: LiveGame
) -> None:
    """The defining behavior of a push transport: connect with no ``since``
    (live-only, per the bus contract), submit a move over plain HTTP, and
    see it arrive over the open socket -- this test never calls
    ``GET /events`` at all."""
    room_id, seats = live_game
    view = client.get(f"{API}/rooms/{room_id}/view", headers=seats[0]).json()
    current = view["current_seat"]
    mover_headers = seats[current]
    token = _token(mover_headers)
    move = client.get(f"{API}/rooms/{room_id}/view", headers=mover_headers).json()[
        "legal_moves"
    ][0]

    with client.websocket_connect(f"{API}/rooms/{room_id}/ws?token={token}") as ws:
        r = client.post(
            f"{API}/rooms/{room_id}/moves", headers=mover_headers, json={"move": move}
        )
        assert r.status_code == 200, r.text

        events = _recv_until(
            ws,
            lambda seen: any(
                e["type"] == "game.move_applied" and e["payload"]["seat"] == current
                for e in seen
            ),
            timeout=5.0,
        )

    applied = [e for e in events if e["type"] == "game.move_applied"]
    assert applied, "move never arrived over the live push transport"
    assert applied[0]["payload"]["move"]["seat"] == current


# -- reconnect / resume -------------------------------------------------


def test_ws_since_resumes_without_missing_or_duplicating_events(
    client: TestClient, live_game: LiveGame
) -> None:
    """A dropped connection resumes from the last seen ``stream_id`` exactly
    like the polling route's own ``since`` -- no gap, no repeat."""
    room_id, seats = live_game
    token = _token(seats[0])

    with client.websocket_connect(
        f"{API}/rooms/{room_id}/ws?token={token}&since=0-0"
    ) as ws:
        first_batch = _recv_until(
            ws, lambda seen: any(e["type"] == "game.started" for e in seen), timeout=5.0
        )
    cursor = first_batch[-1]["stream_id"]

    # Advance the game so there is something new past the cursor.
    view = client.get(f"{API}/rooms/{room_id}/view", headers=seats[0]).json()
    current = view["current_seat"]
    move = client.get(f"{API}/rooms/{room_id}/view", headers=seats[current]).json()[
        "legal_moves"
    ][0]
    client.post(
        f"{API}/rooms/{room_id}/moves", headers=seats[current], json={"move": move}
    )

    rest_since_cursor = client.get(
        f"{API}/rooms/{room_id}/events", headers=seats[0], params={"since": cursor}
    ).json()["events"]
    assert rest_since_cursor, "test setup produced nothing new past the cursor"

    with client.websocket_connect(
        f"{API}/rooms/{room_id}/ws?token={token}&since={cursor}"
    ) as ws:
        resumed = _recv_until(
            ws,
            lambda seen: len(seen) >= len(rest_since_cursor),
            timeout=5.0,
        )

    resumed_ids = [e["event_id"] for e in resumed[: len(rest_since_cursor)]]
    seen_before_ids = {e["event_id"] for e in first_batch}
    assert not (set(resumed_ids) & seen_before_ids), (
        "resumed feed repeated an old event"
    )
    assert resumed[: len(rest_since_cursor)] == rest_since_cursor


# -- ws-ticket (review fix: short-lived ticket instead of the real bearer
# token in the connection URL) ------------------------------------------


def test_ws_ticket_is_bearer_authed(client: TestClient) -> None:
    r = client.get(f"{API}/rooms/00000000-0000-0000-0000-000000000000/ws-ticket")
    assert r.status_code == 401


def test_ws_ticket_works_as_a_ws_token(client: TestClient, live_game: LiveGame) -> None:
    """A ticket minted via the REST endpoint is a real, usable ws token --
    proof the frontend can swap the long-lived bearer token for a
    short-lived one without losing functionality."""
    room_id, seats = live_game
    headers = seats[0]

    ticket = client.get(f"{API}/rooms/{room_id}/ws-ticket", headers=headers).json()[
        "ticket"
    ]
    assert ticket != _token(headers), "ticket must not just echo the real token"

    with client.websocket_connect(
        f"{API}/rooms/{room_id}/ws?token={ticket}&since=0"
    ) as ws:
        events = _recv_until(
            ws, lambda seen: any(e["type"] == "game.started" for e in seen), timeout=5.0
        )
    assert events


def test_ws_since_accepts_bare_seq_not_just_hyphenated_cursor(
    client: TestClient, live_game: LiveGame
) -> None:
    """Review fix: the query pattern used to require ``^\\d+-\\d+$``, so a
    bare ``"0"`` (the bus's own "replay everything" convention,
    docs/events.md) was rejected with a 422 even though the underlying bus
    already treats it as meaningful."""
    room_id, seats = live_game
    token = _token(seats[0])

    with client.websocket_connect(
        f"{API}/rooms/{room_id}/ws?token={token}&since=0"
    ) as ws:
        events = _recv_until(
            ws, lambda seen: any(e["type"] == "game.started" for e in seen), timeout=5.0
        )
    assert events
