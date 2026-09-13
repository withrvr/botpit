"""Permission lookups and role assignment on top of the RBAC tables."""

from __future__ import annotations

import uuid

from sqlmodel import Session, col, or_, select

from app.authz.permissions import ALL_PERMISSIONS
from app.models import (
    Permission,
    Role,
    RoleAssignment,
    RolePermission,
    Room,
    RoomRole,
    User,
    UserRole,
)


def load_permissions(
    session: Session, user_id: uuid.UUID, room_id: uuid.UUID | None = None
) -> frozenset[str]:
    """Permissions of ``user_id`` globally, plus room-scoped ones for ``room_id``.

    One SELECT. Superusers short-circuit to ``ALL_PERMISSIONS`` without a query.
    """
    user = session.get(User, user_id)
    if user is not None and user.is_superuser:
        return ALL_PERMISSIONS
    global_roles = select(UserRole.role_id).where(UserRole.user_id == user_id)
    stmt = (
        select(Permission.name)
        .join(RolePermission, col(RolePermission.permission_id) == Permission.id)
        .join(Role, col(Role.id) == RolePermission.role_id)
    )
    if room_id is None:
        stmt = stmt.where(col(Role.id).in_(global_roles))
    else:
        room_roles = select(RoomRole.role_id).where(
            RoomRole.user_id == user_id, RoomRole.room_id == room_id
        )
        stmt = stmt.where(
            or_(col(Role.id).in_(global_roles), col(Role.id).in_(room_roles))
        )
    return frozenset(session.exec(stmt).all())


def get_role(session: Session, role_name: str) -> Role | None:
    return session.exec(select(Role).where(Role.name == role_name)).first()


def grant_role(
    session: Session,
    user_id: uuid.UUID,
    role_name: str,
    room_id: uuid.UUID | None = None,
) -> None:
    """Idempotent. Raises ``LookupError`` for an unknown role or room,
    ``ValueError`` when ``room_id`` does not match the role's scope."""
    role = get_role(session, role_name)
    if role is None:
        raise LookupError(f"unknown role: {role_name}")
    if (role.scope == "room") != (room_id is not None):
        raise ValueError(f"role {role_name!r} is {role.scope}-scoped")
    if room_id is not None and session.get(Room, room_id) is None:
        # Without this, an unknown room_id only fails at commit (FK violation),
        # which is an unhandled IntegrityError -> 500 instead of a clean 404.
        raise LookupError(f"unknown room: {room_id}")
    if room_id is None:
        if session.get(UserRole, (user_id, role.id)) is None:
            session.add(UserRole(user_id=user_id, role_id=role.id))
    elif session.get(RoomRole, (user_id, role.id, room_id)) is None:
        session.add(RoomRole(user_id=user_id, role_id=role.id, room_id=room_id))
    session.commit()


def revoke_role(
    session: Session,
    user_id: uuid.UUID,
    role_name: str,
    room_id: uuid.UUID | None = None,
) -> bool:
    """Returns False when nothing was assigned. Raises ``LookupError`` for an
    unknown role, ``ValueError`` when ``room_id`` does not match the role's
    scope (matching ``grant_role``'s validation, for symmetric misuse errors)."""
    role = get_role(session, role_name)
    if role is None:
        raise LookupError(f"unknown role: {role_name}")
    if (role.scope == "room") != (room_id is not None):
        raise ValueError(f"role {role_name!r} is {role.scope}-scoped")
    row = (
        session.get(UserRole, (user_id, role.id))
        if room_id is None
        else session.get(RoomRole, (user_id, role.id, room_id))
    )
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_roles(session: Session, user_id: uuid.UUID) -> list[RoleAssignment]:
    global_names = session.exec(
        select(Role.name)
        .join(UserRole, col(UserRole.role_id) == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(col(Role.name))
    ).all()
    room_rows = session.exec(
        select(Role.name, RoomRole.room_id)
        .join(RoomRole, col(RoomRole.role_id) == Role.id)
        .where(RoomRole.user_id == user_id)
        .order_by(col(Role.name), col(RoomRole.room_id))
    ).all()
    return [RoleAssignment(role=n) for n in global_names] + [
        RoleAssignment(role=n, room_id=r) for n, r in room_rows
    ]


def ensure_default_role(session: Session, user: User) -> None:
    """Give a fresh account the ``player`` role.

    No-op when the role row is missing so a database without the RBAC
    migration (or a partially seeded one) still lets accounts be created.
    """
    role = get_role(session, "player")
    if role is None or session.get(UserRole, (user.id, role.id)) is not None:
        return
    session.add(UserRole(user_id=user.id, role_id=role.id))
    session.commit()
