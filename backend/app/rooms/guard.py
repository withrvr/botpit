"""Visibility + own-seat resolution for a room event subscription (docs/rooms.md).

``subscription_guard`` itself talks to neither the database nor the bus --
the HTTP transport (Phase 8's polling routes, Phase 9's archived-history
routes, a future push transport) calls it once per subscribe/request to
build the ``Subscriber`` that ``app.events.filter_for_subscriber`` then
checks per event. ``resolve_subscriber`` below is the thin HTTP-layer
wrapper both `app/api/routes/rooms.py` and `app/api/routes/games.py` share,
so every route that gates on room admission gets the same 404-on-denial
behavior from one place.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import HTTPException, Request
from sqlmodel import Session

from app.authz import P, current_permissions
from app.events import Subscriber
from app.models import Room, RoomSeat, User
from app.rooms.service import RoomService


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


def resolve_subscriber(
    service: RoomService,
    session: Session,
    request: Request,
    user: User,
    room_id: uuid.UUID,
) -> Subscriber:
    """Fetches the room, computes the caller's permissions, and runs
    ``subscription_guard`` -- raising 404 for an unknown room *or* a denied
    subscription, so a private room's existence is never leaked either way."""
    try:
        room = service.get(room_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Room not found") from None
    perms = current_permissions(request, session, user, room_id)
    subscriber = subscription_guard(room, service.seats(room_id), user, perms)
    if subscriber is None:
        raise HTTPException(status_code=404, detail="Room not found")
    return subscriber
