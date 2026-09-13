# Phase 6 — Auth, accounts, roles/permissions

Status: done, branch `phase-6-auth`, 2026-09-12.

Historical snapshot of `backend/app/authz/` and the Redis/rooms
infrastructure this phase delivered ahead of schedule, as they stood when
this phase completed. Not updated for later changes — see
`docs/architecture/latest_architecture.md` for the current state, and
`docs/roles-and-permissions.md`/`docs/capacity-and-queueing.md` for living
documentation of the RBAC and rate-limit/queue systems respectively.

## What was built

Three logical pieces landed in this one phase:

- **`authz`**: roles/permissions tables plus a seed migration (4 default
  roles — `admin`, `player`, `spectator`, room-scoped `moderator` — and 12
  permissions), `load_permissions`, the `require_permission` FastAPI
  dependency, role-administration routes, a default `player` role granted
  on every signup, and `GET /users/me/permissions`.
- **`rooms-infra`**: a Redis client singleton, a Lua-script sliding-window
  rate limiter plus its FastAPI dependency, Crockford base32 join codes,
  and the ZSET-based join queue (offers, sweep, ETA) — all built ahead of
  the phase that actually needed them (rooms, Phase 7) because
  `DECISIONS.md` §1.8/§1.10 assigned these primitives to Phase 7's scope,
  but auth's own account/permission work naturally needed the Redis client
  singleton anyway. CI was updated to start a Redis service.
- **`rooms-tables`**: `Room`/`RoomSeat`/`RuntimeSetting`/`FeatureFlag`/
  `FeatureFlagOverride` tables and their migration (this migration also
  closes the `room_role → room` foreign key that the authz migration had
  to leave deferred, since `room` didn't exist yet when RBAC's own tables
  were created) — plus admin-adjustable runtime settings and feature flags
  with a 5-second per-process cache and a fixed override precedence (user
  > role > global).

## Key implementation decisions made during this phase

- **Permissions are checked; roles are assignment convenience.** Code never
  asks "is this user an admin" — it asks `require_permission(P.SOME_THING)`.
  This is the concrete implementation of the RBAC shape `DECISIONS.md`
  §11 registers, and it's what makes the room-scoped `moderator` role work
  identically to a global role from every call site's point of view.
- **`is_superuser` is a break-glass override, not the normal path.**
  Superusers get every permission without a database query; everyone
  else — including an account holding the `admin` role — goes through the
  actual permission tables. A test specifically proves the `admin` role
  *alone* (without `is_superuser`) is sufficient to open `/roles/`, so the
  two mechanisms don't silently collapse into "just use is_superuser
  everywhere."
- **Deterministic role/permission ids** (`uuid5(NAMESPACE, "role:<name>")`,
  same pattern for permissions) rather than random UUIDs — this makes the
  seed migration idempotent across environments and lets later migrations
  reference `role_id("admin")` directly without a database lookup.
- **Building the Redis/rooms infrastructure ahead of Phase 7's own scope**,
  once it became clear auth's own needs (a Redis singleton, at minimum)
  overlapped heavily with what the build brief had assigned two phases
  later. This left Phase 7 needing only `RoomService`, HTTP routes, the
  subscription guard, and lifespan wiring — a real scope shift recorded
  explicitly in `PROGRESS.md` rather than silently absorbed.
- **Runtime settings and feature flags share one caching shape**: a plain
  dict plus `time.monotonic()`, 5-second TTL, invalidated immediately on
  any write — deliberately not a general-purpose caching library, since
  the actual requirement (single-process cache, no cross-worker
  invalidation needed while running one worker) doesn't justify one. Marked
  in code as a known limit to revisit if a second worker process ships.

## Module/table layout introduced

```
backend/app/authz/
├── permissions.py    # P (permission constants), DEFAULT_ROLES, role_id/permission_id
├── deps.py             # require_permission, current_permissions, global_permissions
└── service.py           # grant_role, revoke_role, list_roles, load_permissions

backend/app/api/routes/roles.py
backend/app/api/routes/admin.py   # runtime settings + feature flags (this phase's rooms-tables work)
```

Tables: `role`, `permission`, `role_permission`, `user_role`, `room_role`
(FK to `room` deferred to this phase's later migration), `runtime_setting`,
`feature_flag`, `feature_flag_override`.

## Tests and what was verified

**651 tests**, ruff/mypy/ty/prek all clean. Housekeeping note from this
phase: Dependabot opened PRs #4–#9 against the template's own
`dependabot.yml` configuration, left for the owner rather than merged as
part of this phase's own scope.
