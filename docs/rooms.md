# Rooms

Status: schema + service + HTTP routes + subscription guard done in Phase 7.
Phase 8 adds the minimal live-play surface a human plays through: `/view`,
`/moves`, `/moves/schema`, `/events` (HTTP polling). Phase 10 adds `/ws`,
a real-time push transport over the identical `subscription_guard`/
`filter_for_subscriber` pipeline — see "Real-time transport" below. The
polling route stays as a working fallback for any client that doesn't
need push.

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
  `"steady"`) against `registry.models` / `get_persona`. A human seat's
  `user_id` may only ever be the creator's own id (seating anyone else here
  would be mass assignment: the seat would show `"filled"` with no
  accept/decline step); seating someone else goes through `invited_user_id`
  + that person calling `join_by_code`. `invited_user_id`, if given, must
  reference a real user. Any failure is a plain `ValueError` (the route
  turns that into 422). Inserts the `Room` with a `new_join_code()` retried
  *only* on a collision with the code's own unique index (any other
  constraint failure propagates instead of being masked as "no code
  available"), then one `RoomSeat` per spec entry (agent seats start
  `status="filled"`; human seats `"filled"` only if `user_id` was given,
  else `"open"`). Grants the creator room-scoped `moderator` via
  `app.authz.service.grant_role` (imported directly, not through
  `app.authz`, to avoid the import cycle that module already avoids
  elsewhere) and publishes `RoomCreated` (`PUBLIC`).
- **`get(room_id) -> Room`** / **`list_public(limit, offset) -> (rooms,
  count)`** — `list_public` returns only `visibility="public"` rooms, newest
  first; `unlisted` is reachable by id/code but never listed.
- **`join_by_code(user, code) -> RoomSeat`** — `normalise_code` first, 404
  (`LookupError`) for an unknown code, `RoomNotWaitingError` once the room
  has left `waiting`. Idempotent (returns the caller's existing seat
  unchanged); otherwise atomically claims the best open seat via
  `_claim_open_seat`: the caller's own reserved invite first, else any open
  seat whose `invited_user_id` is `None` — a seat reserved for a *different*
  invitee is never taken by the fallback, and the claim is a single
  conditional `UPDATE ... WHERE status='open' AND (invited_user_id IS NULL
  OR invited_user_id = :user)` (not a prior `SELECT`), so two callers racing
  for the same seat cannot both win it: whichever commits first gets
  `rowcount == 1`, the other retries against whatever seat is left.
  `RoomFullError` (carrying `room_id`, so the route can attach queue info)
  when nothing is left. Publishes `SeatFilled` (`PUBLIC`).
- **`leave(user, room_id) -> None`** — only while `waiting`; frees the
  caller's own seat back to `kind="human", status="open", user_id=None`. A
  no-op if the caller holds no seat, or the room isn't `waiting`. Does not
  touch the join queue (see "left for later" below).
- **`force_start(user, room_id) -> Room`** — backfills every remaining open
  *human* seat to an agent seat (`registry.default_model`, persona
  `"steady"`), staged but not committed, then calls `start`. If `start`
  raises (capacity exceeded, say), nothing is persisted — `start`'s own
  `seats()` read autoflushes the staged backfill so it still sees it, and
  everything commits together in `start`'s single final commit, or none of
  it does. The caller's permission is checked by the route, not here.
- **`start(room_id) -> Room`** — `RoomNotWaitingError` unless `waiting`,
  `RoomFullError` if any seat is still `open` (backfill first). Builds one
  `SeatSpec` + controller per seat (`HumanController()` for `human` —
  nothing feeds it yet, that's Phase 8; a real `AgentController` wired to
  `registry.client_for(model)` for `agent`; none of this does network I/O).
  Then, under a Postgres advisory transaction lock
  (`pg_advisory_xact_lock`, a fixed key, released automatically at this
  method's commit/rollback) it re-runs `capacity_check()` and re-reads the
  room's status, so a room cannot start once capacity is already spent by
  another `start()` call — this is the actual enforcement point, not
  `create()` (which only stops a room from being *created* past the cap;
  nothing previously stopped an already-created "waiting" room from being
  started past it). The lock serialises this check-then-flip step across
  every worker process, not just this one. Once past it: calls the
  *synchronous* `runner.start(..., on_finished=self._finish_room)` (it
  wraps `asyncio.create_task`, so this must run on the event-loop thread —
  never through `run_in_threadpool`; a `ValueError` from it, an
  already-running game, is translated to `RoomNotWaitingError`), then sets
  `status="in_progress"`, `started_at=now`.
- **`_finish_room(room_id, outcome)`** — `GameRunner`'s completion callback
  (`outcome` is `"finished"` when the referee returned normally, `"stopped"`
  when it was cancelled or raised); see "Room lifecycle" below. Also calls
  `_archive_game` (Phase 9, `docs/games.md`) to copy the game's event log
  into Postgres before it's gone from the live bus for good.
- **`stop(room_id, reason) -> Room`** — cancels the running game via
  `runner.stop` if one is active; a `KeyError` (never started, or already
  stopped) is swallowed. Always sets `status="stopped"`, `finished_at=now`:
  stop is a safe, idempotent operator action regardless of whether a game
  was actually running. Does not conflict with `_finish_room`'s own write
  for the same cancellation (see below).
- **`capacity_check() -> None`** — `CapacityExceededError("global", status)`
  when the count of `status="in_progress"` rooms is at or over
  `settings_store.max_concurrent_games(session)`. `status` is a
  `QueueStatus` from `queue.snapshot("global", ...)` — nobody is actually
  enqueued at create-time in this phase (see below), so it always reads
  `position=None`; the shape is still returned so a future enqueue-on-capacity
  flow is a pure addition, not a response-shape change.
- **`own_seat(user, room_id) -> RoomSeat`** (Phase 8) — the caller's own
  *human* seat in the room, server-derived from `RoomSeat.user_id`, never
  client-supplied. `NotSeatedError` if the caller holds none.
- **`live_view(user, room_id) -> dict`** (Phase 8) — `own_seat` then
  `view_payload(running.game, running.state, seat.seat)` straight from
  `GameRunner`'s in-memory `RunningGame`, never the database (the DB has no
  per-turn game state to read). `RoomNotRunningError` if `GameRunner` has no
  entry for this room (never started, already finished, or stopped).
- **`submit_move(user, room_id, move: dict) -> None`** (Phase 8) — `own_seat`
  + `RoomNotRunningError` as above, plus `NotYourTurnError` if
  `game.current_seat(state) != seat.seat`: submitting anyway would only queue
  silently and be dropped by the next turn's `HumanController.begin_turn()`,
  so this gives an immediate 409 instead of a silent drop. On success,
  forwards the raw move dict to `GameRunner.submit_move` for validation and
  application by the referee.

### Room lifecycle: `waiting` -> `in_progress` -> `finished` / `stopped`

A room's persisted `status` must never silently diverge from whether a game
is actually running under `GameRunner` — otherwise a naturally-finished game
leaves its row `"in_progress"` forever, permanently consuming a slot of
`max_concurrent_games` even though nothing is running. `GameRunner.start(...,
on_finished=callback)` closes this: it spawns a small watcher task that
awaits the same task the referee runs on and calls `callback(room_id,
outcome)` exactly once when it ends (`"finished"` on a normal return,
`"stopped"` on cancellation or an unhandled exception), then self-removes
from `GameRunner._watchers`. `RoomService.start()` passes `self._finish_room`
as that callback, which opens its *own* fresh `Session` (the request that
called `start()` may have long since finished and closed its session) and
only updates a room still `"in_progress"` — an explicit `stop()` already
wrote `"stopped"` itself, and this guard is what stops the two from racing
or double-writing. `GameRunner.close()` (app shutdown) awaits every pending
watcher after cancelling every game, so a graceful shutdown never leaves a
room's DB row out of sync with what a fresh process will see on restart (no
running task, but a row correctly marked `"stopped"` rather than a
lingering `"in_progress"`).

### Real-time transport (Phase 10)

`GET /{room_id}/ws` is a websocket push transport over the exact same
admission and filtering pipeline the polling route uses:
`subscription_guard` resolves the caller's `Subscriber` once per connect
(instead of once per poll), and every event on the bus is run through the
identical `filter_for_subscriber` before being sent — visibility can never
diverge between the two transports, because both call the same functions.

Browsers cannot set a websocket's `Authorization` header, so the JWT
travels as a query parameter instead (`?token=...`), validated with the
same check `get_current_user` uses. Every refusal — a bad or missing
token, an unknown room, a denied subscription — closes the socket with
the identical code (`1008`, policy violation), so a caller can never tell
"wrong token" apart from "no such room" apart from "not invited," matching
the polling route's "never leak existence" 404-either-way rule.

`?since=` resumes a dropped connection exactly like the polling route's
own cursor: omit it for live-only (nothing retained is replayed), pass
`"0-0"` to replay everything retained then continue live, or a previously
seen event's own `stream_id` to resume after it. Full detail:
`docs/api/api-reference.md`'s "Rooms" section; tests:
`backend/tests/rooms/test_rooms_ws.py`.

### Left for later (explicitly, not silently)

- **No admin multi-seat "focus view" yet.** Phase 8's `/view` is one
  caller's own seat only (`own_seat` refuses anyone without a human seat,
  even the creator) — an admin/spectator view across every seat at once,
  built on top of `/ws`'s privileged feed, is still Phase 10 work in
  progress (DECISIONS §10).
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
-> Subscriber | None` resolves visibility + own-seat for one subscription.
Phase 8's `GET /{room_id}/events` is the first real caller: `_subscriber_for`
(in `app/api/routes/rooms.py`) calls it once per poll and feeds the result to
`app.events.filter_for_subscriber` for every event in that page — a
non-subscriber (denied) always gets 404, matching `GET /{room_id}`'s "never
leak existence" rule. A future push transport (Phase 10) calls it once per
connect instead of once per poll; the guard's own logic does not change.

| room visibility | caller | outcome |
|---|---|---|
| any | holds `room:view_seat_detail` | `Subscriber(kind="privileged")` — always, regardless of visibility, seat, or anonymity |
| any | anonymous (`user is None`), no `room:view_seat_detail` | denied (`None`), regardless of `perms` |
| `private` | has a seat in `seats` | `Subscriber(kind="player", seat=n)` |
| `private` | no seat | denied — **no permission fallback at all**, including `room:view_public` and the blanket `room:view` every default account holds; "invited players and admins only" (DECISIONS §1.9) |
| `unlisted` | authenticated, any `perms` | `Subscriber(kind="player", seat=n)` if seated, else `seat=None` — the room id itself ("the link") is the access control per DECISIONS §1.9, no specific permission required |
| `public` | has a seat, or holds `room:view_public` | `Subscriber(kind="player", seat=n or None)` |
| `public` | no seat, no `room:view_public` | denied |

**`room:view` (bare, not `_public`/`_seat_detail`) is never checked here.**
It is granted to every default account (`player`, `spectator`) on signup, so
treating it as sufficient for a private or unlisted room would mean nothing
more than "is a registered user" — exactly the bug this function must not
have. It remains a seeded permission (Phase 6) but is currently unused by
any code path; nothing before this doc's writing checks it.

`unlisted`'s "not listed" property is a *discovery* concern (see
`list_public` above), separate from the subscription decision above: this
function is only reached by a caller who already has the room's id or code,
and once there, being logged in is enough.

## HTTP routes (`app/api/routes/rooms.py`, prefix `/rooms`, tag `rooms`)

`RoomPublic` (already carrying `seats: list[RoomSeatPublic]` in
`app/models.py`) is returned by every route that hands back a room, list or
single — one response shape, no separate "detail" model needed. Seat
identity fields (`user_id`, `invited_user_id`, `model`, `persona`) are
**redacted to `null`** on any seat that is neither the viewer's own nor seen
by a privileged caller (creator or `room:view_seat_detail`); `kind`/`seat`/
`status` (a room's shape) are never redacted. `POST /` and the
creator/moderator-only `start`/`force-start`/`stop` routes always return
full detail (the caller is always privileged there); `GET /` (the public
listing) never reveals any seat's identity to anyone; `GET /{room_id}`
computes `privileged`/`viewer_id` per caller and redacts accordingly; `POST
/join` returns the caller's own seat, always revealed.

| Route | Auth | Notes |
|---|---|---|
| `POST /` | `room:create` + `rate_limit_from_setting("room:create", ...)` | `ValueError` -> 422; `CapacityExceededError` -> 429 (global handler, same pattern as `RateLimited`) |
| `GET /` | `room:view_public` | paginated (`skip`/`limit`), `visibility="public"` only, seat identity always redacted |
| `GET /{room_id}` | authenticated | creator, seated user, `room:view_seat_detail`, or an **`unlisted`** room (any authenticated caller — "viewable with the link") is allowed in; a `public` room additionally needs `room:view_public`; a `private` room with none of the above is 404; every denial is 404 (never a 403, so a room's existence is never leaked) |
| `POST /join` | `room:join` + rate limit | body `{"code": str}`; `LookupError` -> 404; `RoomNotWaitingError` -> 409; `RoomFullError` -> 409 with `{"detail", "queue": {"position", "length", "eta_seconds"}}` from `queue.snapshot(f"room:{room_id}", str(user.id))` |
| `POST /{room_id}/leave` | authenticated only | always safe to call; no-op if not seated |
| `POST /{room_id}/start` | creator, or `room:force_stop` for the room | 409 on `RoomNotWaitingError`/`RoomFullError` |
| `POST /{room_id}/force-start` | same as `start` | backfills then starts |
| `POST /{room_id}/stop` | `room:force_stop` for the room (no creator bypass) | always safe to call |
| `GET /{room_id}/queue` | authenticated | the caller's own `QueueStatus` for `room:{room_id}`; 404 if not queued |
| `GET /{room_id}/view` | own human seat only | the caller's live `PlayerView` (`view_payload`, straight from `GameRunner`, never the DB); `NotSeatedError` -> 403, `RoomNotRunningError` -> 409 |
| `POST /{room_id}/moves` | own human seat only | body `{"move": {...}}`, forwarded verbatim to `GameRunner.submit_move` for the caller's own seat (server-derived, never client-supplied); `NotSeatedError` -> 403, `RoomNotRunningError` -> 409, `NotYourTurnError` -> 409 |
| `POST /{room_id}/chat` | own human seat only + rate limit (`RATE_LIMIT_CHAT`, default `10/10`) | body `{"text": str}` (1-200 chars after stripping); publishes a public `chat.message` from the caller's own seat -- unlike `/moves`, works whether or not a game is running (chat never touches game state); if a game *is* running, also feeds every AI seat's `AgentController.hear()` and tags the event with that game's `correlation.game_id` (Phase 11) |
| `GET /{room_id}/moves/schema` | `subscription_guard`-admitted (seated or privileged) | `app.games.move_schema.move_json_schema(game)` — a terminal client's guide to constructing a valid move body; denial -> 404 |
| `GET /{room_id}/events` | `subscription_guard`-admitted (seated or privileged) | `?since=<stream_id>&limit=200`; polls `EventBus.history` then drops anything `filter_for_subscriber` denies for the caller's resolved `Subscriber`; returns `{"events": [...], "next_since": <last event's stream_id>}` for the next poll; denial -> 404 |

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
