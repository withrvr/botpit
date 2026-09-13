# Rooms

Status: schema + service + HTTP routes + subscription guard done in Phase 7.
The move-submission endpoint and any websocket/SSE transport are explicitly
Phase 8 (see "What Phase 7 does not do" below).

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

## `RoomService` (`app/rooms/service.py`)

Constructor: `RoomService(session, runner: GameRunner, queue: JoinQueue,
registry: ModelRegistry, bus: EventBus)`. Plain exceptions
(`RoomError`/`RoomFullError`/`RoomNotWaitingError`/`CapacityExceededError`),
not `HTTPException`: the route layer (`app/api/routes/rooms.py`) translates
them, same pattern as `admin.py`'s `except LookupError as e: raise
HTTPException(...)`.

Async/sync split (a judgment call the build brief left open, see PROGRESS.md):
a method is `async def` iff it publishes a bus event, awaits the join queue,
or calls another async method; everything else (`get`, `list_public`,
`seats`, `leave`) stays plain `def`. Net result: `create`, `join_by_code`,
`capacity_check`, `force_start`, `start` and `stop` are `async def`.

- **`create(creator, spec: RoomCreate) -> Room`** — capacity-checks first
  (raises `CapacityExceededError` at/over `max_concurrent_games`), validates
  the game slug and seat count (`Game.check_seat_count`), resolves each agent
  seat's model (default `registry.default_model`) and persona (default
  `"steady"`) against `registry.models` / `get_persona`, and each human
  seat's `user_id`/`invited_user_id` against real users — any failure is a
  plain `ValueError` (the route turns that into 422). Inserts the `Room` with
  a `new_join_code()` retried on a unique-constraint collision, then one
  `RoomSeat` per spec entry (agent seats start `status="filled"`; human seats
  `"filled"` only if `user_id` was given, else `"open"`). Grants the creator
  room-scoped `moderator` via `app.authz.service.grant_role` (imported
  directly, not through `app.authz`, to avoid the import cycle that module
  already avoids elsewhere) and publishes `RoomCreated` (`PUBLIC`).
- **`get(room_id) -> Room`** / **`list_public(limit, offset) -> (rooms,
  count)`** — `list_public` returns only `visibility="public"` rooms, newest
  first; `unlisted` is reachable by id/code but never listed.
- **`join_by_code(user, code) -> RoomSeat`** — `normalise_code` first, 404
  (`LookupError`) for an unknown code, `RoomNotWaitingError` once the room
  has left `waiting`. Idempotent (returns the caller's existing seat
  unchanged); otherwise prefers a seat where `invited_user_id == user.id`,
  else the lowest-numbered open human seat; `RoomFullError` (carrying
  `room_id`, so the route can attach queue info) when none is open.
  Publishes `SeatFilled` (`PUBLIC`).
- **`leave(user, room_id) -> None`** — only while `waiting`; frees the
  caller's own seat back to `kind="human", status="open", user_id=None`. A
  no-op if the caller holds no seat, or the room isn't `waiting`. Does not
  touch the join queue (see "left for later" below).
- **`force_start(user, room_id) -> Room`** — backfills every remaining open
  *human* seat to an agent seat (`registry.default_model`, persona
  `"steady"`), then calls `start`. The caller's permission is checked by the
  route, not here.
- **`start(room_id) -> Room`** — `RoomNotWaitingError` unless `waiting`,
  `RoomFullError` if any seat is still `open` (backfill first). Builds one
  `SeatSpec` + controller per seat (`HumanController()` for `human` — nothing
  feeds it yet, that's Phase 8; a real `AgentController` wired to
  `registry.client_for(model)` for `agent`), calls the *synchronous*
  `runner.start(...)` (it wraps `asyncio.create_task`, so this must run on
  the event-loop thread — never through `run_in_threadpool`), then sets
  `status="in_progress"`, `started_at=now`.
- **`stop(room_id, reason) -> Room`** — cancels the running game via
  `runner.stop` if one is active; a `KeyError` (never started, or already
  stopped) is swallowed. Always sets `status="stopped"`, `finished_at=now`:
  stop is a safe, idempotent operator action regardless of whether a game
  was actually running.
- **`capacity_check() -> None`** — `CapacityExceededError("global", status)`
  when the count of `status="in_progress"` rooms is at or over
  `settings_store.max_concurrent_games(session)`. `status` is a
  `QueueStatus` from `queue.snapshot("global", ...)` — nobody is actually
  enqueued at create-time in this phase (see below), so it always reads
  `position=None`; the shape is still returned so a future enqueue-on-capacity
  flow is a pure addition, not a response-shape change.

### Left for later (explicitly, not silently)

- **No move-submission endpoint.** `HumanController` seats are real and
  wired into a real running game, but nothing calls
  `GameRunner.submit_move`; that endpoint is Phase 8's job by design (see
  the Phase 7 build brief).
- **No websocket/SSE transport.** `subscription_guard` (below) is written
  and tested now, but nothing calls it yet — Phase 8/10 wires a transport to
  it.
- **The join queue is read-only in this phase.** `capacity_check` reads
  `queue.snapshot` for its error payload, and the `/rooms/join` route reads
  it for a full room's 409 body, but neither `create` nor `join_by_code`
  ever calls `queue.enqueue`/`offer_next`: nobody actually waits in line
  yet. `docs/capacity-and-queueing.md`'s "a room may start only if running
  games < `MAX_CONCURRENT_GAMES`, else it enqueues on `global`" is not
  implemented as queueing — Phase 7 instead refuses `create` outright
  (`CapacityExceededError`) once at capacity. Wiring real enqueue-and-offer
  behavior on top of the now-tested `JoinQueue` primitives is future work.
- **`leave` does not trigger `offer_next`.** Freeing a seat does not offer
  it to anyone waiting (nobody is waiting yet, per the point above).

## `subscription_guard` (`app/rooms/guard.py`)

`subscription_guard(room, seats, user: User | None, perms: frozenset[str])
-> Subscriber | None` resolves visibility + own-seat for one subscription;
the websocket/SSE transport (Phase 8/10) calls it once per connect and feeds
the result to `app.events.filter_for_subscriber` for every event after that.

| room visibility | caller | outcome |
|---|---|---|
| any | holds `room:view_seat_detail` | `Subscriber(kind="privileged")` — always, regardless of visibility, seat, or anonymity |
| `private` | anonymous (`user is None`) | denied (`None`), even if `perms` is non-empty — private rooms are never guessable by permission alone |
| `private` / `unlisted` | has a seat in `seats` | `Subscriber(kind="player", seat=n)` |
| `private` / `unlisted` | no seat, holds `room:view` | `Subscriber(kind="player", seat=None)` |
| `private` / `unlisted` | no seat, no `room:view` | denied |
| `public` | has a seat | `Subscriber(kind="player", seat=n)` |
| `public` | no seat, holds `room:view` or `room:view_public` | `Subscriber(kind="player", seat=None)` |
| `public` | no seat, neither permission | denied |

`unlisted` is gated identically to `private` here: not being in the public
listing is a *discovery* property (see `list_public` above), not a
subscription one — this function is only reached by a caller who already
has the room's id or code.

A permitted spectator with no seat and no `room:view_seat_detail` is `kind="player", seat=None`
— the same public-only event feed as a seated player, never the privileged
one. Holding a seat always wins regardless of `perms` (even `frozenset()`)
for `private`/`unlisted`/`public` alike, as long as the caller isn't
anonymous on a private room.

## HTTP routes (`app/api/routes/rooms.py`, prefix `/rooms`, tag `rooms`)

`RoomPublic` (already carrying `seats: list[RoomSeatPublic]` in
`app/models.py`) is returned by every route that hands back a room, list or
single — one response shape, no separate "detail" model needed.

| Route | Auth | Notes |
|---|---|---|
| `POST /` | `room:create` + `rate_limit_from_setting("room:create", ...)` | `ValueError` -> 422; `CapacityExceededError` -> 429 (global handler, same pattern as `RateLimited`) |
| `GET /` | `room:view_public` | paginated (`skip`/`limit`), `visibility="public"` only |
| `GET /{room_id}` | authenticated | creator, seated user, or `room:view_seat_detail` always allowed; else a public room needs `room:view_public`; every denial is 404 (never a 403, so a private room's existence is never leaked) |
| `POST /join` | `room:join` + rate limit | body `{"code": str}`; `LookupError` -> 404; `RoomNotWaitingError` -> 409; `RoomFullError` -> 409 with `{"detail", "queue": {"position", "length", "eta_seconds"}}` from `queue.snapshot(f"room:{room_id}", str(user.id))` |
| `POST /{room_id}/leave` | authenticated only | always safe to call; no-op if not seated |
| `POST /{room_id}/start` | creator, or `room:force_stop` for the room | 409 on `RoomNotWaitingError`/`RoomFullError` |
| `POST /{room_id}/force-start` | same as `start` | backfills then starts |
| `POST /{room_id}/stop` | `room:force_stop` for the room (no creator bypass) | always safe to call |
| `GET /{room_id}/queue` | authenticated | the caller's own `QueueStatus` for `room:{room_id}`; 404 if not queued |

`CapacityExceededError`'s 429 body and `RoomFullError`'s 409-on-join body are
both `{"detail": "...", "queue": {"position", "length", "eta_seconds"}}` —
the former via a global exception handler registered in `app/main.py`
(`app.add_exception_handler(CapacityExceededError, capacity_exceeded_handler)`,
mirroring `RateLimited`), the latter built inline in the `/join` route since
only that path needs the room id `RoomFullError` carries.

## `GameRunner` / `ModelRegistry` lifespan wiring (`app/main.py`)

`app.state.runner = GameRunner(app.state.bus)` and `app.state.model_registry
= ModelRegistry.from_settings(settings)` are created right after the event
bus, in the lifespan's `try` block; torn down in the `finally` block in the
order **runner, then registry, then bus** — every running game is cancelled
(which stops its `AgentController`s calling out) before their LLM HTTP
clients are closed, and the bus stays alive until every `room.stopped`
publish from that cancellation has gone out.
