"""Role administration. Every route needs ``admin:manage_roles``."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, select

from app.api.deps import SessionDep
from app.authz import P, list_roles, require_permission
from app.authz import service as authz
from app.models import (
    Message,
    Permission,
    PermissionsPublic,
    Role,
    RoleAssignment,
    RolePublic,
    User,
    UserRolesPublic,
)

router = APIRouter(
    prefix="/roles",
    tags=["roles"],
    dependencies=[Depends(require_permission(P.ADMIN_MANAGE_ROLES))],
)


@router.get("/", response_model=list[RolePublic])
def read_roles(session: SessionDep) -> Any:
    return session.exec(select(Role).order_by(col(Role.name))).all()


@router.get("/permissions", response_model=PermissionsPublic)
def read_permissions(session: SessionDep) -> Any:
    names = session.exec(select(Permission.name).order_by(col(Permission.name))).all()
    return PermissionsPublic(permissions=list(names))


def _get_user(session: SessionDep, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/users/{user_id}", response_model=UserRolesPublic)
def read_user_roles(session: SessionDep, user_id: uuid.UUID) -> Any:
    _get_user(session, user_id)
    return UserRolesPublic(user_id=user_id, roles=list_roles(session, user_id))


@router.post("/users/{user_id}", response_model=UserRolesPublic)
def grant_user_role(
    session: SessionDep, user_id: uuid.UUID, body: RoleAssignment
) -> Any:
    _get_user(session, user_id)
    try:
        authz.grant_role(session, user_id, body.role, body.room_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Role not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return UserRolesPublic(user_id=user_id, roles=list_roles(session, user_id))


@router.delete("/users/{user_id}/{role}", response_model=Message)
def revoke_user_role(
    session: SessionDep,
    user_id: uuid.UUID,
    role: str,
    room_id: uuid.UUID | None = None,
) -> Any:
    _get_user(session, user_id)
    try:
        removed = authz.revoke_role(session, user_id, role, room_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Role not found")
    if not removed:
        raise HTTPException(status_code=404, detail="Role not assigned")
    return Message(message="Role revoked")
