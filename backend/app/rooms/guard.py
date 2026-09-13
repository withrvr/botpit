"""Visibility + own-seat resolution for a room event subscription (docs/rooms.md).

The websocket/SSE transport (Phase 8/10) calls this once per subscribe to
build the ``Subscriber`` that ``app.events.filter_for_subscriber`` then
checks per event; nothing here talks to the database or the bus itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.authz import P
from app.events import Subscriber
from app.models import Room, RoomSeat, User


def subscription_guard(
    room: Room,
    seats: Sequence[RoomSeat],
    user: User | None,
    perms: frozenset[str],
) -> Subscriber | None:
    """``None`` denies the subscription; otherwise the ``Subscriber`` to filter
    events for.

    - ``room:view_seat_detail`` in ``perms`` -> privileged (every seat, full
      detail), regardless of visibility.
    - A private room denies an anonymous caller outright, even one holding
      (nonsensically) some permission -- private rooms are never guessable
      by permission alone.
    - Private/unlisted otherwise need the caller's own seat or ``room:view``.
      Unlisted is not discoverable through listing, but that's a listing
      concern, not a subscription one: this function is reached only by
      someone who already has the room's id or code, so it is gated the
      same as private.
    - Public needs a seat, ``room:view``, or ``room:view_public``.
    - Holding a seat always returns ``Subscriber(kind="player", seat=n)``; a
      permitted spectator with no seat gets ``Subscriber(kind="player",
      seat=None)`` -- the same public-only feed as a seated player, not the
      privileged one.
    """
    if P.ROOM_VIEW_SEAT_DETAIL in perms:
        return Subscriber(kind="privileged")

    if room.visibility == "private" and user is None:
        return None

    seat_number = (
        next((s.seat for s in seats if s.user_id == user.id), None)
        if user is not None
        else None
    )
    has_seat = seat_number is not None

    if room.visibility == "public":
        allowed = has_seat or P.ROOM_VIEW in perms or P.ROOM_VIEW_PUBLIC in perms
    else:  # private, unlisted
        allowed = has_seat or P.ROOM_VIEW in perms

    if not allowed:
        return None
    return Subscriber(kind="player", seat=seat_number)
