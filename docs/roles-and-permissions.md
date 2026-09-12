# Roles and permissions

Status: skeleton, decided and built in Phase 6.

Roles are bundles of permissions (many-to-many: users <-> roles <->
permissions). Permissions are checked, roles are assignment convenience.
Enforcement is central: `require_permission("room:view_seat_detail")` as a
FastAPI dependency on routes and websocket handlers.

Default roles: `admin`, `player`, `spectator`, room-scoped `moderator`.

Library choice (Casbin vs hand-built schema) is researched in Phase 6 and
recorded here.
