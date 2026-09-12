# Roles and permissions

Status: built in Phase 6. Code lives in `backend/app/authz/`.

## The pick: hand-built RBAC, one query, one dependency

Researched in `docs/research/stack-choices.md` §2. The model is small and
fixed: users, roles, permissions, and two assignment tables (global and
per-room). That is five tables and a single question, "which permissions does
user U hold, globally or in room R". Casbin (pycasbin + async SQLAlchemy
adapter) was the runner-up and lost because it adds a second source of truth
(`casbin_rule`), an in-process policy cache that has to be invalidated across
uvicorn workers, and a policy language nobody here needs.

Principles:

- **Permissions are checked, roles are assignment convenience.** Code never
  asks "is this user an admin", it asks `require_permission(P.ROOM_KICK)`.
- **`is_superuser` is a break-glass override**, not the normal path.
  Superusers get `ALL_PERMISSIONS` without a query; everybody else, including
  users holding the `admin` role, goes through the tables. Tests prove that
  the `admin` role alone opens `/roles/`.
- **One central enforcement point** for HTTP (`require_permission`) and one
  function for everything else (`load_permissions`).

## Schema

Migration `a1b2c3d4e5f6_rbac.py` (on top of the template head `fe56fa70289e`).

| table             | columns                                                        | notes                                   |
| ----------------- | -------------------------------------------------------------- | --------------------------------------- |
| `role`            | `id` uuid pk, `name` unique, `scope` `global`/`room`, `description` | ids are `uuid5(NAMESPACE, "role:<name>")` |
| `permission`      | `id` uuid pk, `name` unique, `description`                     | ids are `uuid5(NAMESPACE, "perm:<name>")` |
| `role_permission` | `role_id` fk, `permission_id` fk, pk (both)                    |                                         |
| `user_role`       | `user_id` fk user `ON DELETE CASCADE`, `role_id` fk, pk (both) | global assignments                      |
| `room_role`       | `user_id` fk user `ON DELETE CASCADE`, `role_id` fk, `room_id` uuid, pk (all three), index `(room_id, user_id)` | room-scoped assignments; the FK to `room` arrives with Phase 7 |

Deterministic ids mean the seed is idempotent across environments and later
migrations can reference `role_id("admin")` without a lookup.

Public schemas (`app/models.py`): `RolePublic`, `PermissionsPublic
{permissions: [str]}`, `RoleAssignment {role, room_id | null}`,
`UserRolesPublic {user_id, roles: [RoleAssignment]}`. `User` gets no
relationship attributes; everything goes through `app.authz.service`.

## Seeded roles

Source of truth: `DEFAULT_ROLES` in `backend/app/authz/permissions.py`.
The migration seeds from it with `op.bulk_insert`.

| role        | scope  | permissions                                                                                   |
| ----------- | ------ | --------------------------------------------------------------------------------------------- |
| `admin`     | global | all twelve                                                                                    |
| `player`    | global | `room:create`, `room:join`, `room:play`, `room:chat`, `room:view`, `room:view_public`         |
| `spectator` | global | `room:view`, `room:view_public`                                                               |
| `moderator` | room   | `room:force_stop`, `room:kick`, `room:view_seat_detail`                                       |

Permission names (`class P`): `room:create`, `room:join`, `room:play`,
`room:chat`, `room:view`, `room:view_public`, `room:view_seat_detail`,
`room:force_stop`, `room:kick`, `admin:manage_roles`,
`admin:manage_settings`, `admin:manage_flags`.

Every new account gets `player` (`crud.create_user` calls
`ensure_default_role`, which is a no-op if the role row is missing so a
database that predates the migration still works). Superusers additionally
have everything via the override above.

## HTTP: `require_permission`

```python
from app.authz import P, require_permission

router = APIRouter(
    prefix="/roles",
    dependencies=[Depends(require_permission(P.ADMIN_MANAGE_ROLES))],
)

@router.post("/rooms/{room_id}/stop")
def stop(
    room_id: uuid.UUID,
    user: User = Depends(require_permission(P.ROOM_FORCE_STOP, room_id_param="room_id")),
): ...
```

How it works (`backend/app/authz/deps.py`):

1. `require_permission(name, room_id_param=None)` returns a dependency that
   resolves `CurrentUser` (so unauthenticated requests still get the
   template's 401) and, when `room_id_param` is given, reads the room id
   from `request.path_params`.
2. It calls `current_permissions(request, session, user, room_id)`, which
   memoises `load_permissions` on `request.state.permissions` keyed by room
   id. Several `require_permission` dependencies on one route, plus a
   handler that injects `current_permissions` itself, cost one SELECT.
3. Missing permission raises `403 {"detail": "missing permission: <name>"}`;
   otherwise the dependency returns the `User`.

`load_permissions(session, user_id, room_id=None)` is one SELECT over
`permission ⋈ role_permission ⋈ role` filtered by
`role.id IN (user_role for the user) OR role.id IN (room_role for the user
and room)`. Superusers return `ALL_PERMISSIONS` before the query.

Endpoints:

- `GET /api/v1/users/me/permissions` → `{"permissions": [...]}` (global
  set for the caller).
- `GET /api/v1/roles/`, `GET /api/v1/roles/permissions`,
  `GET|POST /api/v1/roles/users/{user_id}`,
  `DELETE /api/v1/roles/users/{user_id}/{role}?room_id=` — all gated by
  `admin:manage_roles`. Granting a room-scoped role without `room_id`, or a
  global role with one, is a 422; unknown role or user is a 404.

## Websockets and the game loop

Not a `Depends`. At subscribe time the websocket handler does

```python
perms = await run_in_threadpool(load_permissions, session, user.id, room_id)
conn.perms = perms  # frozenset on the connection object
```

and event routing checks `P.ROOM_VIEW_SEAT_DETAIL in conn.perms` per event
with no further database traffic. Live revocation (re-loading on a
`perms_changed:{user_id}` bus event) is a v2 concern; a reconnect picks up
the new set.

## Adding a permission

1. Add the constant to `class P` in `backend/app/authz/permissions.py`
   (`ALL_PERMISSIONS` derives from the class) and put it in the right
   `DEFAULT_ROLES` entries.
2. Write a small data migration that inserts the `permission` row with
   `permission_id(name)` and the `role_permission` rows for the roles that
   should have it. The seed migration only runs once, so existing databases
   need the delta. `alembic check` stays clean since no schema changes.
3. Guard the route with `require_permission(P.NEW_THING)` or check it against
   `conn.perms`.
4. Extend `test_migration_seeds` if the role/permission counts change.

Adding a role is the same shape: a `DEFAULT_ROLES` entry plus a migration
inserting the `role` and `role_permission` rows; assignment then works
through `POST /roles/users/{user_id}` with no code change.
