# botpit — current architecture

> **Keep this current.** Unlike the `phase-NN-*.md` files in this directory
> (historical snapshots, never updated after they're written), this file
> describes the system as it stands on the current branch, read directly
> from the code. Update it as part of every future phase's completion —
> don't let it drift into describing a past state.

As of this writing (branched from `phase-9.5-stabilize-and-rebrand`, itself
branched from `master` right after Phase 9 merged): Phases 0-9 are done.
**Phase 10 (frontend) has not started** — `backend/app/frontend` is empty
except for the template's scaffold, and every "route" below is an HTTP JSON
API with no real-time push transport in front of it yet (Phase 8 built HTTP
polling instead; see `docs/rooms.md`'s "left for later" notes and
`docs/api/api-reference.md` for the polling routes' shapes).

## Module layout (`backend/app/`)

```
app/
├── core/           config.py (Settings), db.py (engine), security.py, redis.py
├── models.py        every table + every request/response schema, one module
├── crud.py           template's user/item CRUD helpers
├── main.py            app factory, lifespan, CORS, exception handlers
├── middleware.py       WideEventMiddleware
├── ratelimit.py         check_rate, RateLimited, rate_limit()/rate_limit_from_setting()
├── flags.py              feature-flag resolution (KNOWN_FLAGS, precedence)
├── settings_store.py       runtime-setting resolution (KNOWN_SETTINGS)
├── initial_data.py          seeds the first superuser (run by scripts/prestart.sh)
├── alembic/                   migration chain (see docs/persistence.md)
│
├── events/          bus.py (protocol), redis_bus.py, filter.py, schema.py (envelope + EVENT_TYPES)
├── games/            base.py (Game contract), cards.py, move_schema.py, + uno_classic/uno_flip/uno_no_mercy/hearts
├── referee/           referee.py, runner.py (GameRunner), seats.py (SeatController impls)
├── agents/              persona.py, prompt.py, controller.py (AgentController)
├── llm/                  client.py (protocol+errors), ollama.py, openai_compat.py, registry.py, fake.py, health.py
├── authz/                 permissions.py (P, DEFAULT_ROLES), deps.py, service.py
├── rooms/                   service.py (RoomService), guard.py (subscription_guard, resolve_subscriber), queue.py (JoinQueue), codes.py
├── telemetry/                 wide.py (wide_event), json_formatter.py, sampling.py
│
└── api/
    ├── main.py        api_router — mounts every router below under API_V1_STR
    ├── deps.py          SessionDep, CurrentUser, RoomServiceDep, JoinQueueDep, get_current_active_superuser
    ├── views.py          view_payload (the HTTP-facing player_view serializer)
    └── routes/
        ├── login.py, users.py, items.py, utils.py, private.py   (template, largely unmodified)
        ├── health.py        (botpit: /health, /health/llm)
        ├── roles.py           (Phase 6: role administration)
        ├── admin.py             (Phase 6/7: runtime settings + feature flags)
        ├── rooms.py               (Phase 7/8: room lifecycle + live play)
        └── games.py                (Phase 9: durable game history)
```

## Data flow

### Room lifecycle

A room moves `waiting → in_progress → finished | stopped`
(`RoomService.create/start/force_start/stop`, `GameRunner`'s completion
watcher calling back into `RoomService._finish_room`). Full detail and the
state diagram: `docs/rooms.md`, `docs/architecture/phase-07-rooms.md`.

### Referee turn loop

`Referee` (per-game, owned by one `GameRunner` asyncio task per room) drives
turn order, validation, application, and win detection; a `SeatController`
(`HumanController`, `AgentController`, `PolicyController`, `ScriptedController`)
is the only interface between the referee and whatever fills a seat. Full
detail and the sequence diagram: `docs/engine.md`,
`docs/architecture/phase-03-referee.md`.

### Event bus and archiving

Every room produces one ordered event stream (`EventEnvelope`, visibility
`public`/`seat(n)`/`privileged`), published through an `EventBus`
(`InMemoryEventBus` or `RedisStreamsEventBus`, picked by `EVENT_BUS`).
`filter_for_subscriber` is the single allow-list privacy filter every
consumer (live polling, archived-history routes, a future push transport)
goes through. When a game ends, `RoomService._archive_game` copies its
entire correlated event history into two Postgres tables
(`GameRecord`/`GameEvent`) so it survives past the live bus's retention —
this is what `GET /games/{id}/events` and `GET /games/{id}/replay` read
from. Full detail: `docs/events.md`, `docs/games.md`.

### Privacy gate

One function, `player_view(state, seat)` (per game, part of the Phase 2
`Game` contract), is the only path from full `GameState` to anything a
seat sees — called by `app.agents.prompt`'s builders for agent prompts and
by `app.api.views.view_payload` for the HTTP payload a human's own seat
receives. Full detail: `docs/engine.md`, `docs/agents.md`.

## HTTP routes (mounted in `backend/app/api/main.py`, all under `API_V1_STR` = `/api/v1`)

| Router | Prefix | Covers |
|---|---|---|
| `login` | (none) | OAuth2 password login, password reset flow |
| `users` | `/users` | account CRUD, signup, `/me`, `/me/permissions` |
| `utils` | `/utils` | test email, health-check (template utility, distinct from botpit's `/health`) |
| `items` | `/items` | template's example CRUD resource (unrelated to botpit's game domain) |
| `health` | `/health` | botpit liveness (`GET /`) and LLM backend status (`GET /llm`, superuser-only) |
| `roles` | `/roles` | RBAC administration — list roles/permissions, grant/revoke a user's role |
| `admin` (+ `me_router`) | `/admin`, `/flags` | runtime settings, feature flags, `/flags/me` |
| `rooms` | `/rooms` | room lifecycle (create/join/leave/start/stop/queue) plus live play (`/view`, `/moves`, `/moves/schema`, `/events`) |
| `games` | `/games` | durable game history: summary, archived events, seat replay |
| `private` | `/private` | dev-only test-user creation, mounted only when `FASTAPI_ENV=development` |

Full endpoint-by-endpoint detail (methods, auth, request/response shapes,
status codes): `docs/api/api-reference.md`.

## Database schema (every table in `backend/app/models.py`)

| Table | Introduced | Purpose |
|---|---|---|
| `user` | template | accounts: email, hashed password, `is_active`, `is_superuser`, `full_name` |
| `item` | template | template's example owned resource |
| `role` | Phase 6 | RBAC role (`scope`: global/room) |
| `permission` | Phase 6 | RBAC permission |
| `role_permission` | Phase 6 | role ↔ permission, many-to-many |
| `user_role` | Phase 6 | global role assignment |
| `room_role` | Phase 6 (FK to `room` added Phase 7) | room-scoped role assignment |
| `room` | Phase 7 | one game instance: code, game slug, seat_count, status, visibility, creator, seed |
| `room_seat` | Phase 7 | one seat: kind (human/agent), occupant, invited user, model/persona |
| `runtime_setting` | Phase 6 | admin-adjustable runtime config, overriding a code default |
| `feature_flag` | Phase 6 | global on/off flag |
| `feature_flag_override` | Phase 6 | per-role or per-user override (XOR-constrained) |
| `game_record` | Phase 9 | one row per finished/stopped game, pk = the referee's `game_id` |
| `game_event` | Phase 9 | durable copy of a game's correlated event log, unique on `(game_id, seq)` |

Full column-level detail: `docs/persistence.md`, `docs/roles-and-permissions.md`,
`docs/rooms.md`, `docs/games.md`.

## Event schema (every type in `backend/app/events/schema.py`'s `EVENT_TYPES`)

| Type | Visibility (as published) | Payload fields |
|---|---|---|
| `room.created` | public | game, seats |
| `room.seat_filled` | public | seat, player |
| `room.stopped` | public | reason |
| `game.started` | public | game, seats, seed, seat_specs |
| `game.turn_started` | public | seat, turn_id, deadline_ms, kind |
| `game.move_applied` | public | seat, move, description, move_index |
| `game.move_rejected` | seat(n) | seat, move, reason, attempt |
| `game.turn_timed_out` | public | seat, fallback_move |
| `game.view_updated` | seat(n) | seat, view (full `PlayerView`) |
| `game.hand_updated` | seat(n) | seat, cards |
| `game.finished` | public | winner_seat, ranking, scores |
| `agent.reasoning` | privileged | seat, model, text |
| `agent.turn_metrics` | registered, not yet published by any code path | seat, model, latency_ms, ok |
| `chat.message` | public | seat, text |

"Visibility" above is what the current publishing call site actually
passes; `game.hand_updated` and `agent.turn_metrics` are registered in
`EVENT_TYPES` (so a subscriber-side consumer can already deserialize them)
but nothing in the codebase publishes either one yet — likely groundwork
for a future per-game `hand_view` event and Phase 13's metrics work,
respectively, not a bug. Visibility is enforced by `filter_for_subscriber`
at read time regardless of what's stamped at publish — a non-privileged
subscriber only ever receives `public` events and events addressed to its
own seat; anything else (including an unrecognized visibility value) is
dropped. Full detail, including which routes call this and how:
`docs/events.md`, `docs/api/api-reference.md`.

## What does not exist yet

- **No real-time push transport.** Every "live" route is HTTP polling.
- **No frontend.** `frontend/` is the template's unmodified scaffold.
- **No leaderboard, chat persona layer beyond agent chat, or admin
  dashboard UI** — these are Phases 10-13's scope.
- **No metrics endpoint** (`/metrics`) yet — Phase 13.
- **No staging or production environment** — see
  `docs/deployment/staging.md` and `docs/deployment/production.md`.
