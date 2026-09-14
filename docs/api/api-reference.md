# API reference

Generated from the actual route and model code, not from memory. Every
route lives under `backend/app/api/routes/`, mounted by
`backend/app/api/main.py`, prefixed with `API_V1_STR` (`/api/v1`, from
`backend/app/core/config.py`). "Auth" below means the route requires a
valid `Authorization: Bearer <JWT>` (via `CurrentUser`/`SessionDep`'s
`get_current_user`), obtained from `POST /login/access-token`.

For the room/game event system's payload shapes and visibility rules, see
"Events and visibility" at the end of this document — that section covers
the live polling route (`GET /rooms/{id}/events`), the live push route
(`WS /rooms/{id}/ws`), and the durable archive route (`GET
/games/{id}/events`) together, since all three share one filtering
mechanism.

## Auth (`login.py`, no route prefix)

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `POST /login/access-token` | none | form-encoded `username`/`password` (`OAuth2PasswordRequestForm`) | `Token {access_token, token_type: "bearer"}` | `400` incorrect email/password or inactive user |
| `POST /login/test-token` | Bearer | — | `UserPublic` | — |
| `POST /password-recovery/{email}` | none | path `email` | `Message` | always `200` (same response whether or not the account exists, to prevent email enumeration) |
| `POST /reset-password/` | none | `NewPassword {token, new_password}` | `Message` | `400` invalid/expired token, inactive user |
| `POST /password-recovery-html-content/{email}` | superuser | path `email` | raw `HTMLResponse` | `404` unknown user |

## Users (`users.py`, prefix `/users`)

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `GET /users/` | superuser | `skip`, `limit` query | `UsersPublic {data, count}` | — |
| `POST /users/` | superuser | `UserCreate` | `UserPublic` | `400` email already exists |
| `PATCH /users/me` | Bearer | `UserUpdateMe {full_name?, email?}` | `UserPublic` | `409` email already exists |
| `PATCH /users/me/password` | Bearer | `UpdatePassword {current_password, new_password}` | `Message` | `400` wrong current password, or new == old |
| `GET /users/me` | Bearer | — | `UserPublic` | — |
| `GET /users/me/permissions` | Bearer | — | `PermissionsPublic {permissions: [str]}` | — |
| `DELETE /users/me` | Bearer | — | `Message` | `403` a superuser may not delete themselves |
| `POST /users/signup` | none | `UserRegister {email, password, full_name?}` | `UserPublic` | `400` email already exists |
| `GET /users/{user_id}` | Bearer | path `user_id` | `UserPublic` | `403` not self and not superuser |
| `PATCH /users/{user_id}` | superuser | `UserUpdate` | `UserPublic` | `404` unknown user, `409` email already exists |
| `DELETE /users/{user_id}` | superuser | — | `Message` | `404` unknown user, `403` a user may not delete themselves this way |

Every new account is granted the default `player` role on creation
(`app.crud.create_user` → `ensure_default_role`) — see "Roles" below.

## Items (`items.py`, prefix `/items`) — template's example resource, unrelated to the game domain

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `GET /items/` | Bearer | `skip`, `limit` query | `ItemsPublic` (own items, or all if superuser) | — |
| `GET /items/{id}` | Bearer | — | `ItemPublic` | `404` not found, `403` not owner and not superuser |
| `POST /items/` | Bearer | `ItemCreate {title, description?}` | `ItemPublic` | — |
| `PUT /items/{id}` | Bearer | `ItemUpdate` | `ItemPublic` | `404`, `403` |
| `DELETE /items/{id}` | Bearer | — | `Message` | `404`, `403` |

## Utils (`utils.py`, prefix `/utils`) — distinct from botpit's own `/health`

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `POST /utils/test-email/` | superuser | query `email_to` | `Message`, `201` | — |
| `GET /utils/health-check/` | none | — | `true` | — |

## Private (`private.py`, prefix `/private`) — mounted only when `FASTAPI_ENV=development`

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `POST /private/users/` | none | `PrivateUserCreate {email, password, full_name, is_verified?}` | `UserPublic` | — |

## Health (`health.py`, prefix `/health`)

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `GET /health/` | none | — | `HealthReport {status, version, env, database, event_bus, llm}` | `503` if DB or event bus check fails (`status: "degraded"`) |
| `GET /health/llm` | superuser | — | `LLMHealth` (reachable, models present/missing, summary) | `503` if the LLM probe is not `.ok` |

`/health/llm` caches its outbound probe for `LLM_PROBE_TTL_SECONDS` (10s)
per worker process — one real probe per TTL window regardless of how many
callers ask.

## Metrics (`metrics.py`, no route prefix) — Phase 13, docs/admin-and-observability.md

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `GET /metrics` | none | — | Prometheus text exposition of `app.metrics.REGISTRY` | — |

Unauthenticated, matching `/health` -- both are operational endpoints for a
scraper/monitor, not user data.

## Roles (`roles.py`, prefix `/roles`) — every route requires `admin:manage_roles`

| Method & path | Request | Response | Status codes |
|---|---|---|---|
| `GET /roles/` | — | `list[RolePublic]` | — |
| `GET /roles/permissions` | — | `PermissionsPublic` | — |
| `GET /roles/users/{user_id}` | — | `UserRolesPublic {user_id, roles: [RoleAssignment]}` | `404` unknown user |
| `POST /roles/users/{user_id}` | `RoleAssignment {role, room_id?}` | `UserRolesPublic` | `404` unknown user/role, `422` scope mismatch (room role without `room_id`, or vice versa) |
| `DELETE /roles/users/{user_id}/{role}` | query `room_id?` | `Message` | `404` unknown user/role or role not assigned, `422` scope mismatch |

## Admin: runtime settings and feature flags (`admin.py`)

Settings/flags routes require `admin:manage_settings`/`admin:manage_flags`
respectively; `/flags/me` is open to any authenticated user.

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `GET /admin/settings` | `admin:manage_settings` | — | `RuntimeSettingsPublic {data: [RuntimeSettingPublic]}` | — |
| `PATCH /admin/settings/{key}` | `admin:manage_settings` | `RuntimeSettingUpdate {value}` | `RuntimeSettingPublic` | `422` unknown key or invalid value |
| `GET /admin/flags` | `admin:manage_flags` | — | `list[FeatureFlagPublic]` | — |
| `PATCH /admin/flags/{key}` | `admin:manage_flags` | `FeatureFlagUpdate {enabled}` | `FeatureFlagPublic` | `404` unknown flag |
| `POST /admin/flags/{key}/overrides` | `admin:manage_flags` | `FeatureFlagOverrideCreate {role?, user_id?, enabled}` | `FeatureFlagOverridePublic` | `404` unknown flag/role/user, `422` neither or both of role/user given |
| `DELETE /admin/flags/{key}/overrides/{override_id}` | `admin:manage_flags` | — | `Message` | `404` unknown override |
| `GET /flags/me` | Bearer | — | `dict[str, bool]` — every `KNOWN_FLAGS` key mapped to its effective value for the caller | — |

Feature-flag precedence: user override > any role override (`true` wins)
> global row; an unknown flag is always `false`. Both settings and flags
are cached in-process for 5 seconds and invalidated immediately on write.

## Rooms (`rooms.py`, prefix `/rooms`)

`RoomPublic` (carrying `seats: list[RoomSeatPublic]`) is the one response
shape for every route that returns a room. Seat identity fields
(`user_id`, `invited_user_id`, `model`, `persona`) are redacted to `null`
on any seat that is neither the caller's own nor visible to a privileged
caller (the room's creator, or a holder of `room:view_seat_detail`);
`kind`/`seat`/`status` are never redacted.

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `POST /rooms/` | `room:create` + rate limit (`RATE_LIMIT_ROOM_CREATE`, default `5/60`) | `RoomCreate {game, seat_count, visibility, seats: [RoomSeatSpec]}` | `RoomPublic` | `422` invalid game/seat_count/seats, `429` capacity exceeded (global handler, body includes `queue` status) |
| `GET /rooms/` | `room:view_public` | `skip`, `limit` query | `RoomsPublic` — `visibility="public"` only, seat identity always redacted | — |
| `GET /rooms/{room_id}` | Bearer | — | `RoomPublic`, seat identity redacted unless privileged/own seat | `404` for both "does not exist" and "exists but denied" (never distinguished) |
| `POST /rooms/join` | `room:join` + rate limit (`RATE_LIMIT_ROOM_JOIN`, default `20/60`) | `RoomJoinRequest {code}` | caller's own `RoomSeatPublic` | `404` unknown code, `409` room not waiting, `409` room full (body includes `queue: {position, length, eta_seconds}`) |
| `POST /rooms/{room_id}/leave` | Bearer | — | `Message` | `404` unknown room; otherwise always `200` (no-op if not seated) |
| `POST /rooms/{room_id}/start` | creator or `room:force_stop` | — | `RoomPublic` | `403` not permitted, `404` unknown room, `409` not waiting / full |
| `POST /rooms/{room_id}/force-start` | same as `start` | — | `RoomPublic` — backfills open human seats to agent seats first | same as `start` |
| `POST /rooms/{room_id}/stop` | `room:force_stop` for the room (no creator bypass) | — | `RoomPublic` | `404` unknown room |
| `GET /rooms/{room_id}/queue` | Bearer | — | `QueueStatus {position, length, eta_seconds}` | `404` not queued |
| `GET /rooms/{room_id}/view` | own human seat only | — | the caller's live `PlayerView` (`view_payload`, from `GameRunner`'s in-memory state, never the database) | `404` unknown room, `403` not seated, `409` game not running |
| `POST /rooms/{room_id}/moves` | own human seat only + rate limit (`RATE_LIMIT_ROOM_MOVE`, default `30/10`) | `MoveSubmission {move: dict}` (≤4096 bytes serialized) | `Message` | `422` oversized body, `404` unknown room, `403` not seated, `409` game not running or not your turn |
| `POST /rooms/{room_id}/chat` | own human seat only + rate limit (`RATE_LIMIT_CHAT`, default `10/10`) | `ChatSend {text: str}` (1-200 chars after stripping) | `Message` | `422` empty/oversized text, `404` unknown room, `403` not seated |
| `GET /rooms/{room_id}/moves/schema` | subscription-guard admitted (seated or privileged) | — | the game's move JSON Schema (`move_json_schema`) | `404` denied or unknown room |
| `GET /rooms/{room_id}/events` | subscription-guard admitted (seated or privileged) | query `since` (pattern `^\d+(-\d+)?$`), `limit` (default 200) | `{"events": [...], "next_since": <cursor>}` | `422` malformed `since`, `404` denied or unknown room |
| `GET /rooms/{room_id}/ws-ticket` | Bearer | — | `{"ticket": <30s JWT>}` — mint just before connecting to `/ws`; a long-lived `access_token` in a URL is a much wider log-exposure surface than a header ever gets | — |
| `WS /rooms/{room_id}/ws` | a `ws-ticket` (or any valid JWT) in `?token=` query param (browsers cannot set a websocket's `Authorization` header); subscription-guard admitted (seated or privileged); rate limited by client host | query `token`, optional `since` (pattern `^\d+(-\d+)?$`, omit for live-only) | a stream of `EventEnvelope` JSON frames, filtered identically to `GET /events` | closes with `1008` (policy violation) for a bad/missing token, unknown room, or denied subscription — the three are never distinguishable, matching `GET /{room_id}`'s 404-either-way rule |

## Games (`games.py`, prefix `/games`) — durable history, Postgres-backed, never the live bus

Every route resolves admission through the identical
`resolve_subscriber`/`subscription_guard` the live `/rooms/{id}/events`
route uses — a finished game's privacy never diverges from its room's own.
An unknown `game_id` and a real game in a room the caller can't see both
produce the identical `404 {"detail": "Game not found"}` (never
distinguished, closing a one-bit side channel a caller who already holds
some `game_id` could otherwise use).

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `GET /games/{game_id}` | Bearer + room-admitted | — | `GameRecordPublic {id, room_id, game, seat_count, outcome, winner_seat, ranking, scores, move_count, started_at, finished_at}` | `404` unknown game or denied |
| `GET /games/{game_id}/events` | Bearer + room-admitted | query `since` (archived `seq` integer, not a live cursor), `limit` (default 200) | `{"events": [...], "next_since": <seq or null>}` | `404` unknown game or denied |
| `GET /games/{game_id}/replay` | Bearer + room-admitted | query `seat?` (default: caller's own), `upto?` (default: every move) | that seat's `PlayerView` at that point, via `Referee.replay` | `400` no `seat` given and caller holds none, `403` requesting another seat without privilege, `422` `upto` exceeds the game's actual move count, `404` unknown game or denied, `500` archived history failed to replay (`ReplayMismatchError`, logged) |

## Leaderboard (`leaderboard.py`, prefix `/leaderboard`) — Phase 12, DECISIONS.md §11

Aggregate, not per-room: joins every `GameRecord` (`outcome == "finished"`
only -- a `"stopped"` game has no `winner_seat`) to `RoomSeat` on `room_id`
(docs/leaderboard.md explains why that join alone is durable and correct).
Gated by `leaderboard:view`, granted to every default role
(`admin`/`player`/`spectator`) -- a leaderboard is meant to be seen, not an
admin-only report.

| Method & path | Auth | Request | Response | Status codes |
|---|---|---|---|---|
| `GET /leaderboard/accounts` | Bearer + `leaderboard:view` | query `game?` (a game slug, e.g. `hearts`; omitted = every game) | `list[LeaderboardEntry {subject_id, display_name, games_played, wins, win_rate}]`, one row per human account, ordered by wins desc | `403` missing the permission |
| `GET /leaderboard/models` | Bearer + `leaderboard:view` | query `game?` | same shape, one row per model name | `403` missing the permission |

## Events and visibility

Both the live polling route (`GET /rooms/{room_id}/events`) and the durable
archive route (`GET /games/{game_id}/events`) return the same envelope
shape and go through the same `filter_for_subscriber` filtering function
(`backend/app/events/filter.py`) — the only difference is where the events
come from (the live `EventBus` vs. archived `GameEvent` rows) and what
`since`/`next_since` mean (a live bus cursor vs. an archived `seq`
integer).

### Envelope shape

```json
{
  "event_id": "…", "room_id": "…", "seq": 12, "type": "game.move_applied",
  "ts": "2026-09-13T00:00:00Z", "visibility": {"kind": "public"},
  "payload": {...}, "correlation": {"game_id": "…"}, "stream_id": "12-0"
}
```

`visibility.kind` is one of `"public"`, `"seat"` (with a `seat: int`
field), or `"privileged"`.

### Who sees what (`filter_for_subscriber`, allow-list only)

| Subscriber | `public` | `seat(n)` | `privileged` |
|---|---|---|---|
| privileged (`room:view_seat_detail`, or the resolving route treats the caller as such) | yes | yes | yes |
| player at seat `n` | yes | yes, only if `n` matches their own seat | no |
| player at seat `m ≠ n`, or unseated spectator | yes | no | no |
| anything unrecognized | no | no | no |

`subscription_guard`/`resolve_subscriber` (`backend/app/rooms/guard.py`)
resolves which `Subscriber` a given caller gets for a given room, per its
visibility (`private`/`unlisted`/`public`) and the caller's own seat and
permissions — see `docs/rooms.md`'s `subscription_guard` table for the
full admission matrix. That resolved `Subscriber` is then applied
uniformly to every event through `filter_for_subscriber`.

### Every registered event type (`backend/app/events/schema.py`'s `EVENT_TYPES`)

| Type | Payload fields | Typically published with |
|---|---|---|
| `room.created` | `game`, `seats` | `public` |
| `room.seat_filled` | `seat`, `player` | `public` |
| `room.stopped` | `reason` | `public` |
| `game.started` | `game`, `seats`, `seed`, `seat_specs` | `public` |
| `game.turn_started` | `seat`, `turn_id`, `deadline_ms`, `kind` | `public` |
| `game.move_applied` | `seat`, `move`, `description`, `move_index` | `public` |
| `game.move_rejected` | `seat`, `move`, `reason`, `attempt` | `seat(n)` — only the mover learns why |
| `game.turn_timed_out` | `seat`, `fallback_move` | `public` |
| `game.view_updated` | `seat`, `view` (full `PlayerView`) | `seat(n)` |
| `game.hand_updated` | `seat`, `cards` | `seat(n)` (declared; not yet published by any code path) |
| `game.finished` | `winner_seat`, `ranking`, `scores` | `public` |
| `agent.reasoning` | `seat`, `model`, `text` | `privileged` — published before the move it explains is returned |
| `agent.turn_metrics` | `seat`, `model`, `latency_ms`, `ok` | registered; not yet published by any code path |
| `chat.message` | `seat`, `text` | `public` |

Every event carries `correlation.game_id` when it originates from a
`Referee`/`AgentController` turn — this is what
`RoomService._archive_game` (Phase 9) uses to decide what belongs in a
game's durable history versus a room-lifecycle event that has no game
association (`room.created`, `room.seat_filled`).
