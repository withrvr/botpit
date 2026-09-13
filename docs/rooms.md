# Rooms

Status: skeleton, filled in during Phase 7.

A room is one instance of a game: game type, seat list (each seat human
open / human invited / AI model+persona), status
(waiting / in_progress / finished / stopped), join code, visibility
(private / unlisted / public), creator (implicit room moderator).

Hard caps enforced server-side: seat count, viewers per room, concurrent
games, total connections. Overflow joins a FIFO queue. See
`docs/capacity-and-queueing.md`.

## Schema (Phase 7)

- `room`: `id` (uuid pk), `code` (unique join code, indexed), `game`,
  `seat_count`, `status` (waiting / in_progress / finished / stopped),
  `visibility` (private / unlisted / public), `creator_id` (FK `user.id`,
  `SET NULL` — the creator is the implicit room moderator, see §11),
  `seed` (bigint, deterministic referee replay per §3), `created_at`,
  `started_at`, `finished_at`. Indexed on `(status, visibility)` for the
  public lobby listing and on `created_at` for recency ordering.
- `room_seat`: `id` (uuid pk), `room_id` (FK `room.id`, `CASCADE`), `seat`
  (0-based index, unique per room), `kind` (human / agent), `user_id` (FK
  `user.id`, `SET NULL` — the human currently in the seat), `invited_user_id`
  (FK `user.id`, `SET NULL` — a specific human invited to claim an open
  seat), `model` / `persona` (agent seats only), `status`
  (open / filled). Unique on `(room_id, seat)`.
- `room_role` (from Phase 6) gets its deferred FK to `room.id` (`CASCADE`)
  added in this phase's migration, now that `room` exists.

## Runtime settings and feature flags

Both admin-adjustable-at-runtime patterns follow the "row overrides the
default" shape from `docs/research/stack-choices.md` §4: nothing is source
of truth except the database, in-process reads are cached for 5 s
(`app.settings_store`, `app.flags` — plain dict + `time.monotonic()`, no
extra dependency), and every write invalidates the cache immediately.
`# ponytail: per-process cache, Redis invalidate when multi-worker` — fine
while the app runs as one worker (§6 Prometheus multi-worker caveat notes
the same constraint); add a `flags_changed`/`settings_changed` Redis pub/sub
invalidation if a second worker ships.

- **Runtime settings** (`runtime_setting`: `key` pk, `value` JSON,
  `updated_at`, `updated_by`) back `MAX_CONCURRENT_GAMES`, `MAX_CONNECTIONS`,
  `MAX_VIEWERS_PER_ROOM`. `KNOWN_SETTINGS` maps each key to a default getter
  (falls back to `app.core.config.settings`), a description, and a
  validator (`int >= 1`). Unset key -> config default; set row -> overrides
  it. `GET/PATCH /admin/settings` guarded by `admin:manage_settings`.
- **Feature flags** (`feature_flag` + `feature_flag_override`, schema exactly
  per stack-choices §4) start at `KNOWN_FLAGS` (`rooms.public_listing`,
  `chat.enabled`, `admin.log_tail`), seeded lazily — the row is created on
  first read so the admin panel lists every known flag without a data
  migration. Precedence: **user override > any role override (true wins) >
  global row**; an unknown flag is always off. `GET/PATCH /admin/flags` plus
  `POST`/`DELETE` on `/admin/flags/{key}/overrides` guarded by
  `admin:manage_flags`; `GET /flags/me` is open to any authenticated user
  and returns `{flag: effective_bool}` for the frontend to gate on.

## Service and queue (later phase)

TODO: `RoomService` (create/join/start/stop), seat allocation, and the
Redis join-queue integration (`app/rooms/queue.py`) land in the Phase 8
service/routes task. This doc's schema section above is the data layer they
build on.
