"""FastAPI glue: per-request permission cache and ``require_permission``."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import HTTPException, Request

from app.api.deps import CurrentUser, SessionDep
from app.authz.service import load_permissions
from app.models import User


def current_permissions(
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    room_id: uuid.UUID | None = None,
) -> frozenset[str]:
    """``load_permissions`` for the current user, cached on ``request.state``
    so several dependencies on one route share a single SELECT."""
    cache: dict[uuid.UUID | None, frozenset[str]]
    try:
        cache = request.state.permissions
    except AttributeError:
        cache = request.state.permissions = {}
    if room_id not in cache:
        cache[room_id] = load_permissions(session, user.id, room_id)
    return cache[room_id]


def global_permissions(
    request: Request, session: SessionDep, user: CurrentUser
) -> frozenset[str]:
    """The current user's global (non-room-scoped) permissions.

    Use this, not ``current_permissions`` directly, as a route dependency
    whenever the contract is "my global permissions": ``current_permissions``
    takes an optional ``room_id``, and FastAPI would otherwise expose that as
    an undocumented, caller-controlled query parameter on the route.
    """
    return current_permissions(request, session, user, None)


def require_permission(
    name: str, room_id_param: str | None = None
) -> Callable[..., User]:
    """Dependency factory: 403 unless the current user holds ``name``.

    With ``room_id_param`` the room id is read from that path parameter so
    room-scoped roles (moderator) count too. Returns the user.
    """

    def dependency(request: Request, session: SessionDep, user: CurrentUser) -> User:
        room_id: uuid.UUID | None = None
        if room_id_param is not None:
            try:
                room_id = uuid.UUID(str(request.path_params[room_id_param]))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="invalid room id") from exc
        if name not in current_permissions(request, session, user, room_id):
            raise HTTPException(status_code=403, detail=f"missing permission: {name}")
        return user

    return dependency
