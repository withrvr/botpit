"""DB-backed tests for roles, permissions and ``require_permission``."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.authz import (
    ALL_PERMISSIONS,
    DEFAULT_ROLES,
    P,
    current_permissions,
    grant_role,
    load_permissions,
    require_permission,
    revoke_role,
)
from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.models import Permission, Role, RolePermission, User
from tests.utils.room import create_room
from tests.utils.user import create_random_user
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
PLAYER = DEFAULT_ROLES["player"][2]


def _normal_user(db: Session) -> User:
    user = db.exec(select(User).where(User.email == settings.EMAIL_TEST_USER)).one()
    return user


# --- seeds ---------------------------------------------------------------


def test_migration_seeds(db: Session) -> None:
    roles = {r.name: r for r in db.exec(select(Role)).all()}
    assert set(roles) == {"admin", "player", "spectator", "moderator"}
    assert roles["moderator"].scope == "room"
    assert {p.name for p in db.exec(select(Permission)).all()} == ALL_PERMISSIONS
    assert len(ALL_PERMISSIONS) == 12
    admin_perms = db.exec(
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)  # type: ignore[arg-type]
        .where(RolePermission.role_id == roles["admin"].id)
    ).all()
    assert set(admin_perms) == ALL_PERMISSIONS


# --- load_permissions ----------------------------------------------------


def test_load_permissions_player_superuser_unknown(db: Session) -> None:
    player = create_random_user(db)
    assert load_permissions(db, player.id) == PLAYER
    superuser = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    assert load_permissions(db, superuser.id) == ALL_PERMISSIONS
    assert load_permissions(db, uuid.uuid4()) == frozenset()


def test_room_scoped_moderator_only_for_its_room(db: Session) -> None:
    user = create_random_user(db)
    room, other = create_room(db).id, create_room(db).id
    grant_role(db, user.id, "moderator", room)
    mod = DEFAULT_ROLES["moderator"][2]
    assert load_permissions(db, user.id, room) == PLAYER | mod
    assert load_permissions(db, user.id, other) == PLAYER
    assert load_permissions(db, user.id) == PLAYER
    assert revoke_role(db, user.id, "moderator", room)
    assert load_permissions(db, user.id, room) == PLAYER


def test_new_users_get_player(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    for headers, path in (
        (None, "/users/signup"),
        (superuser_token_headers, "/users/"),
    ):
        r = client.post(
            f"{API}{path}",
            headers=headers,
            json={"email": random_email(), "password": random_lower_string()},
        )
        assert r.status_code == 200, r.text
        assert load_permissions(db, uuid.UUID(r.json()["id"])) == PLAYER


# --- HTTP ------------------------------------------------------------------


def test_me_permissions(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    assert client.get(f"{API}/users/me/permissions").status_code == 401
    r = client.get(f"{API}/users/me/permissions", headers=normal_user_token_headers)
    assert r.status_code == 200
    assert r.json() == {"permissions": sorted(PLAYER)}


def test_roles_admin_gate(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    r = client.get(f"{API}/roles/", headers=normal_user_token_headers)
    assert r.status_code == 403
    assert r.json() == {"detail": f"missing permission: {P.ADMIN_MANAGE_ROLES}"}
    r = client.get(f"{API}/roles/", headers=superuser_token_headers)
    assert r.status_code == 200
    assert {x["name"] for x in r.json()} == set(DEFAULT_ROLES)
    r = client.get(f"{API}/roles/permissions", headers=superuser_token_headers)
    assert set(r.json()["permissions"]) == ALL_PERMISSIONS

    # grant admin to the normal user: roles, not is_superuser, drive access
    user = _normal_user(db)
    r = client.post(
        f"{API}/roles/users/{user.id}",
        headers=superuser_token_headers,
        json={"role": "admin"},
    )
    assert r.status_code == 200, r.text
    assert {"role": "admin", "room_id": None} in r.json()["roles"]
    assert (
        client.get(f"{API}/roles/", headers=normal_user_token_headers).status_code
        == 200
    )
    r = client.get(f"{API}/roles/users/{user.id}", headers=normal_user_token_headers)
    assert r.json()["user_id"] == str(user.id)

    r = client.delete(
        f"{API}/roles/users/{user.id}/admin", headers=superuser_token_headers
    )
    assert r.status_code == 200
    assert (
        client.get(f"{API}/roles/", headers=normal_user_token_headers).status_code
        == 403
    )
    r = client.delete(
        f"{API}/roles/users/{user.id}/admin", headers=superuser_token_headers
    )
    assert r.status_code == 404


def test_grant_validation(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    user = create_random_user(db)
    url = f"{API}/roles/users/{user.id}"
    h = superuser_token_headers
    assert client.post(url, headers=h, json={"role": "nope"}).status_code == 404
    assert (
        client.post(
            f"{API}/roles/users/{uuid.uuid4()}", headers=h, json={"role": "admin"}
        ).status_code
        == 404
    )
    assert client.post(url, headers=h, json={"role": "moderator"}).status_code == 422
    assert (
        client.post(
            url, headers=h, json={"role": "admin", "room_id": str(uuid.uuid4())}
        ).status_code
        == 422
    )
    room = create_room(db).id
    r = client.post(url, headers=h, json={"role": "moderator", "room_id": str(room)})
    assert r.status_code == 200
    assert {"role": "moderator", "room_id": str(room)} in r.json()["roles"]
    r = client.delete(f"{url}/moderator", headers=h, params={"room_id": str(room)})
    assert r.status_code == 200


# --- require_permission with a room path param + per-request cache --------

_router = APIRouter(prefix="/_authz_test", tags=["_authz_test"])


@_router.get(
    "/rooms/{room_id}/stop",
    dependencies=[
        Depends(require_permission(P.ROOM_FORCE_STOP, room_id_param="room_id"))
    ],
)
def _stop(room_id: uuid.UUID) -> dict[str, str]:
    return {"room_id": str(room_id)}


@_router.get(
    "/twice",
    dependencies=[
        Depends(require_permission(P.ROOM_VIEW)),
        Depends(require_permission(P.ROOM_JOIN)),
    ],
)
def _twice(perms: Any = Depends(current_permissions)) -> dict[str, int]:
    return {"n": len(perms)}


@_router.get(
    "/rooms_raw/{room_id}/stop",
    dependencies=[
        Depends(require_permission(P.ROOM_FORCE_STOP, room_id_param="room_id"))
    ],
)
def _stop_raw(room_id: str) -> dict[str, str]:
    # room_id kept as str (not uuid.UUID) so a malformed value reaches
    # require_permission's own path-param parsing untouched by FastAPI's
    # own coercion, exercising the 422 branch directly.
    return {"room_id": room_id}


app.include_router(_router, prefix=API)


def test_require_permission_room_param(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    user = _normal_user(db)
    room, other = create_room(db).id, create_room(db).id
    grant_role(db, user.id, "moderator", room)
    h = normal_user_token_headers
    assert (
        client.get(f"{API}/_authz_test/rooms/{room}/stop", headers=h).status_code == 200
    )
    assert (
        client.get(f"{API}/_authz_test/rooms/{other}/stop", headers=h).status_code
        == 403
    )
    revoke_role(db, user.id, "moderator", room)


def test_require_permission_malformed_room_id_is_422(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    h = normal_user_token_headers
    r = client.get(f"{API}/_authz_test/rooms_raw/not-a-uuid/stop", headers=h)
    assert r.status_code == 422


def test_permissions_cached_per_request(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    perm_selects = 0

    def count(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        nonlocal perm_selects
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "role_permission" in statement
        ):
            perm_selects += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        r = client.get(f"{API}/_authz_test/twice", headers=normal_user_token_headers)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert r.status_code == 200
    assert r.json() == {"n": len(PLAYER)}
    assert perm_selects == 1
