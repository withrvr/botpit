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
    - ``private``: "invited players and admins only" (DECISIONS.md §1.9) --
      the caller's own seat, or nothing. Deliberately does **not** fall back
      to any permission: ``room:view`` is granted to every default account
      on signup (``player``, ``spectator``), so treating it as sufficient
      here would let any registered user who merely learns a private room's
      id watch it forever -- the exact bug this function must not have.
    - ``unlisted``: "viewable with the link, not listed publicly" -- the
      link (the room id, reaching this function at all) is the access
      control, so any authenticated caller is admitted; anonymous is not.
    - ``public``: "listed and viewable by any account with spectator
      permission" -- the caller's own seat, or ``room:view_public``.
    - Holding a seat always returns ``Subscriber(kind="player", seat=n)``; a
      permitted spectator with no seat gets ``Subscriber(kind="player",
      seat=None)`` -- the same public-only feed as a seated player, not the
      privileged one.
    """
    if P.ROOM_VIEW_SEAT_DETAIL in perms:
        return Subscriber(kind="privileged")

    if user is None:
        return None  # every remaining branch requires an authenticated caller

    seat_number = next((s.seat for s in seats if s.user_id == user.id), None)
    has_seat = seat_number is not None

    if room.visibility == "private":
        allowed = has_seat
    elif room.visibility == "unlisted":
        allowed = True  # authenticated + reached this room's id: that's the gate
    else:  # public
        allowed = has_seat or P.ROOM_VIEW_PUBLIC in perms

    if not allowed:
        return None
    return Subscriber(kind="player", seat=seat_number)
