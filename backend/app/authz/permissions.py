"""Permission names and the seeded default roles.

Import-light on purpose: the Alembic data migration imports this module, so it
must not pull in ``app.models`` or the database engine.
"""

from __future__ import annotations

import uuid
from typing import Literal

Scope = Literal["global", "room"]

NAMESPACE = uuid.UUID("0f7b1c2e-6d3a-4b8e-9c1f-5a2d7e4b6c90")


class P:
    ROOM_CREATE = "room:create"
    ROOM_JOIN = "room:join"
    ROOM_PLAY = "room:play"
    ROOM_CHAT = "room:chat"
    ROOM_VIEW = "room:view"
    ROOM_VIEW_PUBLIC = "room:view_public"
    ROOM_VIEW_SEAT_DETAIL = "room:view_seat_detail"
    ROOM_FORCE_STOP = "room:force_stop"
    ROOM_KICK = "room:kick"
    ADMIN_MANAGE_ROLES = "admin:manage_roles"
    ADMIN_MANAGE_SETTINGS = "admin:manage_settings"
    ADMIN_MANAGE_FLAGS = "admin:manage_flags"
    LEADERBOARD_VIEW = "leaderboard:view"


ALL_PERMISSIONS: frozenset[str] = frozenset(
    v for k, v in vars(P).items() if not k.startswith("_")
)

# name -> (scope, description, permissions)
DEFAULT_ROLES: dict[str, tuple[Scope, str, frozenset[str]]] = {
    "admin": ("global", "Full access to everything.", ALL_PERMISSIONS),
    "player": (
        "global",
        "Can create, join and play in rooms.",
        frozenset(
            {
                P.ROOM_CREATE,
                P.ROOM_JOIN,
                P.ROOM_PLAY,
                P.ROOM_CHAT,
                P.ROOM_VIEW,
                P.ROOM_VIEW_PUBLIC,
                P.LEADERBOARD_VIEW,
            }
        ),
    ),
    "spectator": (
        "global",
        "Can watch rooms.",
        frozenset({P.ROOM_VIEW, P.ROOM_VIEW_PUBLIC, P.LEADERBOARD_VIEW}),
    ),
    "moderator": (
        "room",
        "Per-room moderation: stop the game, kick seats, see seat detail.",
        frozenset({P.ROOM_FORCE_STOP, P.ROOM_KICK, P.ROOM_VIEW_SEAT_DETAIL}),
    ),
}


def role_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"role:{name}")


def permission_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"perm:{name}")
