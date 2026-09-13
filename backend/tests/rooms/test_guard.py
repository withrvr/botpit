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
VIEW_PUBLIC = frozenset({P.ROOM_VIEW_PUBLIC})
SEAT_DETAIL = frozenset({P.ROOM_VIEW_SEAT_DETAIL})
# `room:view` is granted to every default account (player, spectator) on
# signup; it must never by itself unlock a private or unlisted room, or it
# would mean nothing more than "is a registered user". See the guard's own
# docstring and DECISIONS.md §1.9.
BLANKET_VIEW = frozenset({P.ROOM_VIEW})


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


@pytest.mark.parametrize("visibility", ["private", "unlisted", "public"])
def test_anonymous_is_always_denied_without_seat_detail(visibility: str) -> None:
    """No permission set admits an anonymous caller into any room; only
    ``room:view_seat_detail`` (checked before ``user is None``) can."""
    room = _room(visibility)
    assert subscription_guard(room, [], None, NONE) is None
    assert subscription_guard(room, [], None, VIEW_PUBLIC) is None
    assert subscription_guard(room, [], None, BLANKET_VIEW) is None


def test_private_needs_a_seat_and_nothing_else() -> None:
    room = _room("private")
    user = _user()
    assert subscription_guard(room, [], user, NONE) is None
    assert subscription_guard(room, [], user, VIEW_PUBLIC) is None
    # The bug this guards against: the blanket `room:view` every account
    # holds must not be treated as "invited".
    assert subscription_guard(room, [], user, BLANKET_VIEW) is None

    _assert_player(subscription_guard(room, [_seat(room, user)], user, NONE), seat=0)


def test_unlisted_admits_any_authenticated_caller() -> None:
    """ "Viewable with the link, not listed publicly" (DECISIONS §1.9): the
    room id itself is the access control once a caller is logged in, no
    specific permission required."""
    room = _room("unlisted")
    user = _user()
    _assert_player(subscription_guard(room, [], user, NONE), seat=None)
    _assert_player(subscription_guard(room, [], user, VIEW_PUBLIC), seat=None)
    _assert_player(subscription_guard(room, [], user, BLANKET_VIEW), seat=None)
    _assert_player(subscription_guard(room, [_seat(room, user)], user, NONE), seat=0)


def test_public_needs_a_seat_or_room_view_public() -> None:
    room = _room("public")
    user = _user()
    assert subscription_guard(room, [], user, NONE) is None
    # The blanket `room:view` is not "spectator permission" (room:view_public
    # is); dropping it here matches DECISIONS §1.9's literal wording and the
    # private-room fix above for consistency.
    assert subscription_guard(room, [], user, BLANKET_VIEW) is None

    _assert_player(subscription_guard(room, [], user, VIEW_PUBLIC), seat=None)
    _assert_player(subscription_guard(room, [_seat(room, user)], user, NONE), seat=0)


def test_a_seat_belonging_to_someone_else_does_not_count() -> None:
    room = _room("private")
    seated_user = _user()
    onlooker = _user()
    seats = [_seat(room, seated_user)]
    assert subscription_guard(room, seats, onlooker, NONE) is None
    assert subscription_guard(room, seats, onlooker, BLANKET_VIEW) is None
