"""Table test for ``subscription_guard`` (docs/rooms.md): every combination
of visibility x seated/unseated x permission set x anonymous the docstring
describes. No database, no Redis: it is a pure function."""

from __future__ import annotations

import uuid

import pytest

from app.authz import P
from app.events import Subscriber
from app.models import Room, RoomSeat, User
from app.rooms.guard import subscription_guard


def _assert_player(result: Subscriber | None, seat: int | None) -> None:
    assert result is not None
    assert result.kind == "player"
    assert result.seat == seat


def _room(visibility: str) -> Room:
    return Room(
        id=uuid.uuid4(),
        code="ABCDEFGH",
        game="uno_classic",
        seat_count=2,
        visibility=visibility,
        seed=1,
    )


def _user() -> User:
    return User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", hashed_password="x"
    )


def _seat(room: Room, user: User) -> RoomSeat:
    return RoomSeat(
        id=uuid.uuid4(),
        room_id=room.id,
        seat=0,
        kind="human",
        user_id=user.id,
        status="filled",
    )


NONE: frozenset[str] = frozenset()
VIEW = frozenset({P.ROOM_VIEW})
VIEW_PUBLIC = frozenset({P.ROOM_VIEW_PUBLIC})
SEAT_DETAIL = frozenset({P.ROOM_VIEW_SEAT_DETAIL})


@pytest.mark.parametrize("visibility", ["private", "unlisted", "public"])
def test_view_seat_detail_is_privileged_regardless_of_everything(
    visibility: str,
) -> None:
    room = _room(visibility)
    result = subscription_guard(room, [], None, SEAT_DETAIL)
    assert result is not None
    assert result.kind == "privileged"


@pytest.mark.parametrize("visibility", ["private", "unlisted", "public"])
def test_view_seat_detail_beats_no_seats_list_too(visibility: str) -> None:
    room = _room(visibility)
    user = _user()
    result = subscription_guard(room, [_seat(room, user)], user, SEAT_DETAIL)
    assert result is not None
    assert result.kind == "privileged"


def test_private_anonymous_is_always_denied_even_with_permissions() -> None:
    room = _room("private")
    # room:view alone would normally admit a seatless spectator; anonymity
    # on a private room vetoes that outright per the guard's own contract.
    assert subscription_guard(room, [], None, VIEW) is None
    assert subscription_guard(room, [], None, VIEW_PUBLIC) is None
    assert subscription_guard(room, [], None, NONE) is None


@pytest.mark.parametrize("visibility", ["private", "unlisted"])
def test_private_and_unlisted_need_a_seat_or_room_view(visibility: str) -> None:
    room = _room(visibility)
    user = _user()
    assert subscription_guard(room, [], user, NONE) is None
    assert (
        subscription_guard(room, [], user, VIEW_PUBLIC) is None
    )  # public perm insufficient

    _assert_player(subscription_guard(room, [], user, VIEW), seat=None)
    _assert_player(subscription_guard(room, [_seat(room, user)], user, NONE), seat=0)


def test_public_needs_a_seat_room_view_or_room_view_public() -> None:
    room = _room("public")
    user = _user()
    assert subscription_guard(room, [], user, NONE) is None

    _assert_player(subscription_guard(room, [], user, VIEW_PUBLIC), seat=None)
    _assert_player(subscription_guard(room, [], user, VIEW), seat=None)
    _assert_player(subscription_guard(room, [_seat(room, user)], user, NONE), seat=0)


def test_public_anonymous_still_needs_a_qualifying_permission() -> None:
    """Unlike private, public has no explicit anonymous veto in the guard's
    contract: an (unrealistic, since perms come from an authenticated caller)
    anonymous+room:view_public combination is allowed by the letter of the
    spec. Documented here rather than silently "fixed"."""
    room = _room("public")
    assert subscription_guard(room, [], None, NONE) is None
    _assert_player(subscription_guard(room, [], None, VIEW_PUBLIC), seat=None)


def test_a_seat_belonging_to_someone_else_does_not_count() -> None:
    room = _room("private")
    seated_user = _user()
    onlooker = _user()
    seats = [_seat(room, seated_user)]
    assert subscription_guard(room, seats, onlooker, NONE) is None
    _assert_player(subscription_guard(room, seats, onlooker, VIEW), seat=None)
