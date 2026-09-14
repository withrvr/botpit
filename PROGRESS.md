# PROGRESS

> Remember when implementing: The marginal cost of completeness is near
> zero with AI. Do the whole thing. Do it right. Do it with tests. Do it
> with documentation. Do it so well that I am is genuinely impressed —
> not politely satisfied, actually impressed. Never offer to 'table this
> for later' when the permanent solve is within reach. Never leave a
> dangling thread when tying it off takes five more minutes. Never
> present a workaround when the real fix exists. The standard isn't
> 'good enough' — it's 'holy shit, that's done.' Search before building.
> Test before shipping. Ship the complete thing. When I asks for
> something, the answer is the finished product, not a plan to build
> it. Time is not an excuse. Fatigue is not an excuse. Complexity is not
> an excuse. Boil the ocean.

## How to resume

Read `DECISIONS.md`, then this file, then the `docs/*.md` for the subsystem
you are about to touch. Branch naming: phase branches are `phase-N-<slug>`,
task branches are `task/phase-N/<slug>` (git refuses `phase-N-slug/task-x`
when `phase-N-slug` exists as a branch). Task branches merge into the phase
branch; only the phase branch opens a PR into `master`.

## Status

| Phase | Name | Status |
|---|---|---|
| 0 | Research, decisions, proposal | done 2026-09-12 |
| 1 | Scaffold, event schema, environments | done 2026-09-12 (PR #1) |
| 2 | Game contract + UNO, Flip, No Mercy, Hearts | done pending PR |
| 2b | Adopt full-stack-fastapi-template as base (owner decision 2026-09-12) | done 2026-09-12 (PR #3) |
| 3 | Deterministic referee, mixed seats | done 2026-09-12 (PR #10) |
| 4 | Privacy gate, prompt builder, human redaction | done 2026-09-12 (PR #10) |
| 5 | Agent client, swappable backend | done 2026-09-12 (PR #10) |
| 6 | Auth, accounts, roles/permissions | done 2026-09-12 (PR #11) |
| 7 | Rooms, seats, capacity, join queue | done 2026-09-13 (PR #12) |
| 8 | First live mixed game (terminal + API) | done 2026-09-13 (PR #13) |
| 9 | Persistence and replay | done 2026-09-13 (PR #14) |
| 9.5 | Stabilize, document, rebrand | done 2026-09-13 (PR #15) |
| 9.6 | Upgrade to latest stable versions | done 2026-09-13 (PR #17) |
| 9.7 | Docker, CI/CD, and server-start optimization | done 2026-09-14 (PR #18) |
| 10 | Frontend: play, spectate, admin, focus view | done 2026-09-14 (PR #20) |
| 11 | Chat and personas | done 2026-09-14 (PR #21) |
| 12 | Leaderboard | done 2026-09-14 (branch phase-12-leaderboard, PR pending) |
| 13 | Observability | |
| 14 | CI/CD (local dry-run; no server yet) | |
| 15 | Production deployment (deferred until local complete) | |
| 16 | Scale and polish | |

## Phase 7 — done 2026-09-13 (branch phase-7-rooms)

Everything PROGRESS.md's Phase 6 note said Phase 7 still needed: `RoomService`,
HTTP routes, `SubscriptionGuard`, `GameRunner`/`ModelRegistry` lifespan wiring.

- `app/rooms/service.py`: `RoomService` (`create`, `get`, `list_public`,
  `join_by_code`, `leave`, `force_start`, `start`, `stop`, `capacity_check`)
  and its exceptions (`RoomError`, `RoomFullError`, `RoomNotWaitingError`,
  `CapacityExceededError`). `start`/`force_start` assemble and run a *real*
  live game: a real `Referee` via `GameRunner.start`, real `HumanController`s
  (unfed — Phase 8 adds `submit_move`), real `AgentController`s backed by the
  real `ModelRegistry`. `create` grants the creator room-scoped `moderator`
  and publishes `RoomCreated`; `join_by_code` publishes `SeatFilled`.
- `app/rooms/guard.py`: `subscription_guard` — visibility + own-seat
  resolution for the (not-yet-built) websocket/SSE transport, table-tested
  now against every combination in its own docstring.
- `app/api/routes/rooms.py`: `POST /`, `GET /`, `GET /{room_id}`, `POST
  /join`, `POST /{room_id}/leave`, `POST|/{room_id}/start`,
  `/force-start`, `/stop`, `GET /{room_id}/queue`. `CapacityExceededError`
  registered as a global exception handler (429, same pattern as
  `RateLimited`); a private room's existence is never leaked (403 vs 404).
- `app/main.py`: `app.state.runner` (`GameRunner`) and
  `app.state.model_registry` (`ModelRegistry`) created after the event bus
  in the lifespan, torn down in the order runner -> registry -> bus.
- Judgment calls made where the build brief left the sync/async split open
  (documented in `docs/rooms.md` and in code comments): `create`,
  `join_by_code`, `force_start`, `capacity_check` ended up `async def` (they
  publish events / await the join queue / call another async method) even
  though the brief's illustrative signatures showed some of them as plain
  `def`; `get`, `list_public`, `leave`, `seats` stayed sync.
- Deferred, precisely (not silently): the move-submission endpoint
  (`GameRunner.submit_move`) and any websocket/SSE transport are Phase 8's
  job per the build brief. The join queue's `enqueue`/`offer_next` are not
  wired into `create`/`join_by_code`/`leave` in this phase — `capacity_check`
  and the `/join` 409 body only *read* `queue.snapshot`; nobody actually
  waits in line yet (see `docs/rooms.md` "Left for later").
- Tests: `tests/rooms/{test_room_service,test_rooms_api,test_guard}.py`, 50
  new tests (12 guard, 25 service, 13 HTTP), all passing against real
  Postgres + Redis. Full suite: 705 tests, ruff/ruff-format/mypy
  strict/ty/prek all clean.

### Review fixes (two independent reviews: correctness/concurrency, security)

- **Room lifecycle**: nothing previously demoted a naturally-finished game
  out of `"in_progress"`, permanently leaking capacity over time.
  `GameRunner.start(..., on_finished=...)` now spawns a watcher that fires
  exactly once when the game's task ends; `RoomService._finish_room` opens
  its own session (the request session may be long closed) and updates a
  room still `"in_progress"` to `"finished"` or `"stopped"`; `close()`
  awaits every pending watcher on shutdown. New `finished` status is now
  actually reachable, closing the gap `docs/rooms.md` always documented.
- **Capacity enforced at `start()`, not only `create()`** (CRITICAL,
  security review): a caller could otherwise accumulate many `waiting`
  rooms within `create()`'s rate limit, then call the unthrottled `start()`
  on all of them, each firing real outbound LLM calls. `start()` now
  re-checks capacity under a Postgres advisory transaction lock
  (`pg_advisory_xact_lock`), serialising the check-then-flip step across
  every worker process.
- **Mass assignment fixed**: a `RoomSeatSpec.user_id` may now only be the
  creator's own id; seating anyone else goes through `invited_user_id` +
  that person joining themselves.
- **Join-fallback reservation bug fixed**: an uninvited joiner's fallback
  seat pick now excludes any seat still reserved (`invited_user_id` set for
  someone else), and the whole claim is one atomic conditional `UPDATE`
  (rowcount-checked) instead of a Python check-then-write race.
- **`subscription_guard` privacy bug fixed**: `private`/`unlisted` rooms no
  longer fall back to the blanket `room:view` permission every default
  account holds (that made "private" mean nothing more than "is a
  registered user"). Rewritten per DECISIONS §1.9's literal wording:
  `private` needs a seat, full stop; `unlisted` admits any authenticated
  caller (the link is the gate); `public` needs a seat or
  `room:view_public`.
- **`GET /{room_id}` no longer reveals every seat's identity** to any
  caller who merely clears the visibility gate: seat `user_id`/
  `invited_user_id`/`model`/`persona` are redacted to `null` except for the
  viewer's own seat or a privileged caller (creator / `room:view_seat_detail`).
  Unlisted rooms are now reachable by any authenticated caller at this
  route too, matching the guard fix above.
- Also fixed: `_insert_room_with_unique_code` now retries only on the
  code's own unique-index collision (not any `IntegrityError`, which was
  masking real failures as "no code available"); a bad `room_id` on
  `grant_role`/`set_override` now 404s instead of surfacing as an unhandled
  `IntegrityError`; a concurrent first read of an unseeded feature flag no
  longer raises; `force_start`'s seat backfill no longer commits ahead of
  `start()` succeeding (a failure used to leave human seats permanently
  converted to agent seats).
- New tests: `tests/unit/referee/test_runner.py` (the completion watcher,
  fast via `PolicyController` — no real LLM calls needed), plus targeted
  additions to `test_room_service.py`/`test_rooms_api.py`/`test_guard.py`
  for every fix above. Full suite: 720 tests, gate clean.

## Phase 8 — done 2026-09-13 (branch phase-8-live-game)

The minimal live-play surface a human plays through: `/view`, `/moves`,
`/moves/schema`, `/events`. HTTP polling, not a websocket/SSE transport (that
stays Phase 10's job) — this is the first real caller of Phase 7's
`subscription_guard`.

- `app/rooms/service.py`: three new `RoomService` methods and exceptions.
  `own_seat(user, room_id) -> RoomSeat` resolves the caller's own human seat
  server-side from `RoomSeat.user_id` (`NotSeatedError` if none — never
  trusts a client-supplied seat number). `live_view(user, room_id) -> dict`
  reads the caller's live `PlayerView` straight from `GameRunner`'s
  in-memory `RunningGame` via `app.api.views.view_payload` (never the DB,
  which holds no per-turn state) — `RoomNotRunningError` if `GameRunner` has
  no entry (never started, finished, or stopped). `submit_move(user,
  room_id, move: dict) -> None` adds a `NotYourTurnError` check against
  `game.current_seat(state)` before forwarding to
  `GameRunner.submit_move`, so an out-of-turn submission gets an immediate
  409 instead of silently queuing and being dropped by the next turn's
  `HumanController.begin_turn()`.
- `app/api/routes/rooms.py`: `GET /{room_id}/view`, `POST /{room_id}/moves`,
  `GET /{room_id}/moves/schema`, `GET /{room_id}/events`. A shared
  `_subscriber_for` helper wraps `subscription_guard` + `current_permissions`
  for `moves/schema` and `events` (denial -> 404, matching every other
  room route's "never leak existence" rule); `view`/`moves` use `own_seat`
  instead (denial -> 403, since these are about playing your own seat, not
  administrative visibility). `events` polls `EventBus.history(room_id,
  since, limit)` and drops anything `filter_for_subscriber` denies for the
  resolved `Subscriber`, returning `{"events": [...], "next_since": ...}`
  for the next poll.
- Deferred, precisely: a real push transport (websocket/SSE) and an
  admin/spectator multi-seat "focus view" are both Phase 10's job — the
  polling routes are a working, tested fallback either way, not a stub.
- Tests: `tests/rooms/test_rooms_live.py`, 11 new tests against a real
  2-human-seat `uno_classic` game (real `GameRunner`, no mocks): auth-gate
  smoke tests per route (`NotSeatedError`/`RoomNotRunningError`/
  `NotYourTurnError` -> 403/409/409), a full submit-and-verify-via-`/events`
  happy path, and two security tests proving `game.view_updated` (a seat's
  full hand, `SeatVisibility`) never reaches the other seat through
  `/events` for a non-privileged player, while the privileged creator sees
  both. Full suite: 731 tests, ruff/ruff-format/mypy strict/ty/prek all
  clean.

### Review fixes (two independent reviews: correctness/concurrency, security)

- **`GameRunner` never evicted a finished game** (HIGH, correctness): a
  naturally-finished or stopped room's `RunningGame` stayed in `_games`
  forever, so `GET /view`/`POST /moves` kept finding a dead game instead of
  ever raising `RoomNotRunningError` -- a move submitted after the game
  ended could look "accepted" (200) with nobody left to read it. Also a
  per-room memory leak. Fixed at the one place that knows a game truly
  ended: `GameRunner._watch` now pops `self._games[room_id]` alongside the
  existing watcher cleanup, so `get()`'s already-correct `KeyError` path
  (used by every caller) actually fires. Regression tests:
  `tests/unit/referee/test_runner.py` asserts `_games` eviction after both
  a natural finish and a `stop()`, plus a new end-to-end test proving
  `get`/`submit_move` raise `KeyError` once a game is gone.
- **Cross-thread `asyncio.Queue.put_nowait`** (HIGH, both reviews): `POST
  /moves` is a sync route (Starlette threadpool thread) calling
  `HumanController.submit`, which the referee's `decide()` concurrently
  awaits on the event-loop thread -- `asyncio.Queue` is documented safe only
  for same-thread use or `call_soon_threadsafe`. Fixed: `HumanController`
  captures its loop at construction (always on the loop thread, inside
  `RoomService.start`) and `submit` now marshals through
  `loop.call_soon_threadsafe`.
- **Unbounded move queue** (CRITICAL, security): nothing capped how many
  or how large the dicts a client could pile into `HumanController`'s queue
  before the referee drained one, and `POST /moves` had no rate limit at
  all (unlike `POST /`/`POST /join`). Fixed three ways: `HumanController`'s
  queue is now `maxsize=1` with replace-not-grow semantics (the latest
  submission always wins over one not yet consumed -- only one is ever
  meaningful); `MoveSubmission.move` is size-capped at 4096 bytes
  (a `field_validator`, -> 422 over the limit); a new
  `RATE_LIMIT_ROOM_MOVE` setting (30/10s) gates the route the same way
  `room:create`/`room:join` already are. New tests: `test_seats.py`
  (queue bound/replace semantics), `test_rooms_live.py` (oversized body ->
  422, rate limit -> 429).
- **Unvalidated `since` on `/events`** (MEDIUM, security): a malformed
  cursor reached `int(since.split("-", 1)[0])` unguarded and 500'd instead
  of 400ing. Fixed with a query-parameter pattern (`^\d+-\d+$`, matching
  every cursor this app ever emits) -> 422 on anything else. New test in
  `test_rooms_live.py`.
- Reviewed and accepted as-is (no change needed): seat/turn identity is
  always server-derived (no mass-assignment vector in `MoveSubmission`);
  `filter_for_subscriber` is applied unconditionally to every event with no
  bypass path; `/moves/schema` carries no per-seat state; `next_since`
  advancing past a hidden event is a deliberate, documented tradeoff (the
  alternative -- never advancing -- would let a caller get stuck polling
  forever behind an event they can't see).
- Full suite after fixes: 737 tests, gate clean (two `prek` passes -- the
  frontend SDK regenerates once for the four new routes, then holds).

## Phase 9 — done 2026-09-13 (branch phase-9-game-history)

DECISIONS.md §12: "the event log is the source of truth; finished games
replay from it." Two Postgres tables and the HTTP surface over them --
`docs/games.md` has the full design; this is the summary.

- `app/models.py`: `GameRecord` (`game_record`, pk = the referee's own
  `game_id`, FK to `room.id` `CASCADE`) and `GameEvent` (`game_event`, FK to
  `game_record.id` `CASCADE`, unique on `(game_id, seq)`), migration
  `f54e8ebd4583_game_history_phase_9.py` (autogenerated cleanly against the
  two new tables only).
- `app/rooms/service.py`: `_archive_game`, hooked into `_finish_room`
  (`GameRunner`'s completion callback). Reads the room's full event history
  off the live bus, keeps only events carrying a `correlation.game_id`
  (everything the referee published, including its own `room.stopped` on
  cancellation -- `RoomService`'s own `room.created`/`room.seat_filled`
  carry no game_id and are excluded), builds one `GameRecord` from the
  `game.started`/`game.finished` payloads, and archives every kept event as
  a `GameEvent`. Plus `get_game_record`/`list_game_records`/`game_events`
  read methods.
- `app/rooms/guard.py`: `resolve_subscriber`, the HTTP-layer admission
  wrapper around Phase 7's `subscription_guard`, moved here (public) from
  `app/api/routes/rooms.py`'s private `_subscriber_for` so the new `/games`
  routes reuse the exact same room-admission check the live `/events` route
  already uses -- one place, not two that could drift.
- `app/api/routes/games.py` (new router, prefix `/games`): `GET
  /{game_id}` (summary), `GET /{game_id}/events` (the durable counterpart
  to the live route -- same `filter_for_subscriber` call, reconstructing an
  `EventEnvelope` from each archived row), `GET /{game_id}/replay?seat=&upto=`
  (rebuilds one seat's `PlayerView` at any point via the existing
  `Referee.replay`, truncating the archived move log to `upto` moves;
  same own-seat-only privacy rule as the live `/view`).
- **Review-caught-by-self bug, fixed before this ever reached a reviewer**:
  the first draft nested `_archive_game` *inside* `_finish_room`'s "only
  write if still `in_progress`" guard. That guard exists because `stop()`
  races this exact callback (both await the same cancelled task; either
  can resume first) and must not double-write `room.status` -- but the
  callback still fires exactly once per game regardless of who wins that
  race, so nesting archiving inside it meant archiving was silently
  skipped every time `stop()` happened to win. Caught by writing (and
  running) `tests/rooms/test_game_history.py` before considering the phase
  done, not by luck: the HTTP-level "stop a room, check it archived" tests
  happened to pass either way depending on real scheduling order, so a
  dedicated deterministic regression test was added --
  `test_finish_room_archives_even_when_stop_already_won_the_status_race`
  constructs the room already `"stopped"` (simulating `stop()` having
  already won) *before* calling `_finish_room`, which fails against the
  original nested version and passes against the fix. Verified both
  directions by hand (temporarily reintroducing the exact buggy guard,
  confirming the test fails with the expected `LookupError`, then
  restoring the fix).
- Also fixed during the same pass: the archive's `GameRecord` +
  `GameEvent` inserts hit a `ForeignKeyViolation` under SQLAlchemy's
  default flush ordering (no explicit `Relationship()` between the two
  models for it to infer dependency order from); fixed with an explicit
  `session.flush()` between adding the record and adding its events,
  forcing correct ordering rather than relying on the flush planner.
- Deferred, precisely: no retry/backlog for a failed archive (logged, not
  re-attempted); no pagination past a generous `limit=100_000` on either
  the live-bus read or the archived-row read.
- Tests: `tests/rooms/test_game_history.py`, 14 new tests against a real
  stopped `uno_classic` game (real `GameRunner`, real Postgres): archiving
  correctness (record fields, event filtering, seq ordering), the race
  regression above, auth-gate/404 smoke tests per route, event-visibility
  redaction (a non-privileged seat never sees the other seat's archived
  `game.view_updated`; the creator sees both), pagination via `next_since`,
  and replay's own-seat-default/cross-seat-403/no-seat-400/upto-422 paths.
  Full suite: 751 tests, ruff/ruff-format/mypy strict/ty all clean.

### Review fixes (two independent reviews: correctness/concurrency, security)

- **Agent-published events never archived** (HIGH, correctness): the
  race fix above was self-caught before review; this one wasn't.
  `AgentController._publish` stamped no `correlation` at all, so every
  `agent.reasoning`, `agent.turn_metrics`, and agent-authored `chat.message`
  was silently dropped by `_archive_game`'s `correlation.game_id` filter --
  contradicting `docs/games.md`'s own claim that `agent.*`/`chat.*` get
  archived. Root cause: `AgentController` is constructed before
  `Referee.__init__` generates its `game_id`, so it had nothing to stamp
  with. Fixed by threading it through: `AgentController` gains a
  `game_id: str | None` attribute (starts `None`); `Referee.__init__`
  duck-type-sets it on any controller that declares the attribute (same
  pattern as the existing `say`/`hear` hooks), right after generating its
  own `game_id`; `_publish` now includes `correlation={"game_id": ...}`
  once set. New regression test:
  `test_referee_threads_its_game_id_into_agent_controllers` in
  `tests/unit/referee/test_referee.py`.
- **Inconsistent 404 message between "unknown game" and "game exists but
  room denies you"** (LOW, security): two different `detail` strings for
  the same status code let a caller who already holds a `game_id`
  distinguish the two cases -- a one-bit side channel, and a regression
  against `get_room`'s own explicit "never distinguish denied from does
  not exist" rule. Fixed with a shared `_admitted_record` helper in
  `games.py` that unifies both failure branches to the identical
  `"Game not found"` 404 across all three routes. New test:
  `test_unknown_game_and_denied_game_404_with_the_same_body`.
- **Archiving completeness depends on the live bus's own retention**
  (MEDIUM, correctness, documented not code-changed): both `EventBus`
  implementations bound each room to `EVENT_STREAM_MAXLEN` (10,000)
  events; `_archive_game` only sees whatever is still there when the game
  ends. Ten thousand events for one game is enormous headroom for these
  games' actual lengths, so left as a documented dependency
  (`docs/games.md` "Left for later") rather than a code change -- the
  cleaner fix (archive incrementally during play, not only at the end)
  is future work, not urgent.
- Reviewed and accepted as-is (no change needed): the explicit
  `session.flush()` before adding `GameEvent` rows is correct and
  sufficient (confirmed the whole archive is one transaction -- a later
  failure rolls back the `GameRecord` insert too, no orphaned summary);
  `replay`'s unfiltered fetch of `game.move_applied` rows is safe because
  that event type is always published `PUBLIC` (verified in
  `referee.py::_commit`), and the only channel back to the caller is the
  seat-scoped `player_view`, never raw state or the raw move list; no
  realistic IDOR/enumeration risk via `game_id` (a 122-bit `uuid4().hex`,
  and every route re-derives `room_id` and re-checks admission
  regardless); the three `/games` GET routes carrying no rate limit
  matches every other read-only room route in this codebase (`GET
  /rooms/{id}`, `/queue`, `/view`, `/moves/schema`, the live `/events`) --
  not a gap introduced here.
- Full suite after fixes: 753 tests, gate clean.

## Phase 9.5 — done 2026-09-13 (branch phase-9.5-stabilize-and-rebrand)

A deliberate pause between backend work and the frontend (Phase 10):
stabilize, document, and rebrand everything built so far. No new game
features. Done with the planner's structure but the planning itself done
directly rather than through a dedicated planner-agent call (full context
of all nine prior phases was already in hand; a fresh planner subagent
would only have re-derived it from this same PROGRESS.md at real cost for
no added judgment) -- git worktrees and parallel subagents were used for
the three genuinely independent pieces per the brief.

- **Dependency audit** (`docs/development/dependency-audit.md`): backend
  `uv lock --upgrade` (30 packages within existing ranges: fastapi,
  sqlalchemy, sqlmodel, starlette, uvicorn, pydantic, alembic, psycopg,
  the dev toolchain), frontend `bun update` (react/react-query/router,
  tailwindcss, biome, vite, ~20 packages). Two major-version bumps
  (`@tanstack/react-table` v8->v9, `typescript` v6->v7) deliberately left
  for Phase 10 to decide, since they'd need real code migration in files
  Phase 10 is about to touch anyway; `axios`/`form-data`'s exact pins left
  alone (no record of why they're pinned, not worth risking). Python
  3.14 consistency confirmed (`.python-version` read by every CI workflow
  that needs it). Full suite green after (753 tests), frontend build/lint
  clean.
- **Documentation reorganized** into `docs/`: `docs/architecture/` gets
  one historical-snapshot file per completed phase (00 through 9, plus
  2b for the template-adoption pivot) mined from this file's own
  per-phase records, plus `latest_architecture.md` (always-current, read
  fresh from the actual code -- module layout, data flow, every HTTP
  route, every DB table, every event type). `docs/api/api-reference.md`
  generated from the real route/model code, not memory. `docs/stack.md`
  consolidates the old `docs/research/stack-choices.md` (deleted).
  `docs/development/setup.md` and `docs/deployment/{staging,production}.md`
  fold in real content from three stray root docs (`development.md`,
  `deployment.md`, `deployment-docker-compose.md`, all deleted), rewritten
  for botpit with no template placeholders -- staging/production stated
  plainly as *planned*, not live, per the standing no-server-until-local
  constraint. `release-notes.md` (the upstream template's own changelog)
  deleted outright. Everything else already under `docs/` was reviewed
  and left in place (already matched the target structure) except
  `docs/persistence.md`, which was rewritten -- it was stale, still
  describing Phase 1's async-SQLAlchemy/SQLite design that Phase 2b
  superseded. `DECISIONS.md`/`PROGRESS.md` stay at the repo root as the
  project's permanent decision/status logs, not "documentation to
  reorganize" -- not named in the target structure and moving them would
  break every cross-reference in this whole build.
- **`scripts/dev.sh`**: one-command tmux launcher (session `botpit-dev`;
  windows for the backend with reload, the frontend dev server, Docker
  Compose Postgres/Redis, and container logs). Idempotent (attaches to an
  existing session instead of erroring), guards the frontend window in
  case it's ever unscaffolded again, and was actually run twice (not just
  syntax-checked) to confirm every window starts cleanly and the
  idempotency check works.
- **Rebranding**: removed every trace of the template's own cosmetic
  identity -- package names (`backend/pyproject.toml`, root and frontend
  `package.json`), the frontend's title/favicon/logo/footer credits (the
  FastAPI lightning-bolt logo and its four SVG assets deleted, replaced
  with a plain "botpit" wordmark -- no fabricated visual identity
  invented), nine template screenshot/preview images under root `img/`
  (deleted, unreferenced by the rewritten README). `DECISIONS.md`,
  `PROGRESS.md`, and `.github/workflows/*` deliberately untouched -- the
  first two legitimately document the template-adoption decision as
  history, and the workflows' template references are a standing,
  recorded deferral to Phase 14 (this file's own Phase 2b note), not an
  oversight.
- **Root files**: `README.md` rewritten marketing-facing (what botpit is,
  a quickstart pointer to `docs/development/setup.md`, links out to
  `docs/` for everything technical, no deployed-instance claims).
  `CONTRIBUTING.md` rewritten with the real branch/PR/test conventions.
  Added `LICENSE` (MIT), `CODE_OF_CONDUCT.md` (Contributor Covenant
  v2.1), `SECURITY.md` (private GitHub security-advisory reporting, given
  real auth/RBAC now exists), and `llms.txt` (a factual, non-marketing
  project summary for crawlers/LLM tools -- explicitly notes HTTP-polling
  only and no frontend yet, so it can't go stale-by-omission).
- **Two real regressions caught by re-running the full gate after every
  merge, not assumed away**: renaming `backend/pyproject.toml`'s package
  off `"app"` broke hatchling's default wheel file-selection heuristic
  (it looks for a source directory matching the project name; the actual
  tree is still `app/`) -- fixed with an explicit
  `[tool.hatch.build.targets.wheel] packages = ["app"]`. Renaming
  `frontend/package.json`'s package off `"frontend"` broke every
  `bun run --filter frontend ...` call site this repo's own tooling
  depends on (root `package.json`'s scripts, `scripts/generate-client.sh`,
  and the `prek` "Generate Frontend SDK" hook that calls them) -- fixed
  by updating those call sites to `--filter botpit-frontend` (leaving
  `.github/workflows/deploy.yml`'s identical stale reference alone, per
  the same Phase 14 deferral as above).
- **Not done, explicitly, per the brief's own instruction to say so
  rather than add unused placeholders**: `robots.txt`, `sitemap.xml`, and
  Open Graph/meta tags do not exist yet. They only make sense once
  Phase 10's frontend exists and is actually deployed somewhere
  crawlable, and neither is true yet -- there is no frontend and no
  hosted instance (a standing project constraint, not an oversight).
  Revisit this once Phase 10 ships and, separately, once staging/
  production actually exist.
- Full gate after every merge in this phase: `ruff check`/`mypy app`/
  `ty check app` clean, backend suite 753 tests green, frontend
  `bun run build`/`bun run lint` clean, `prek run --all-files` clean.

## Phase 9.6 — done 2026-09-13 (branch phase-9.6-dependency-upgrade)

One job: every dependency onto its true latest stable version, verified,
falling back only where genuinely broken with the reason documented. Full
detail: `docs/development/dependency-audit.md`.

- Backend: `uv lock --upgrade` picked up the small remainder Phase 9.5
  left (`ast-serialize`, `coverage`, `pydantic-core` patches); raised two
  `pyproject.toml` floors (`redis[hiredis]` to `>=8.1.0`,
  `psycopg[binary]` to `>=3.3.5,<4.0.0`) to close Dependabot PRs #8/#9 for
  real. `pydantic-core` 2.49.0 exists but `pydantic` 2.13.5 (itself the
  true latest) doesn't request it yet -- not a gap, verified against PyPI
  directly.
- Frontend: attempted every deferral Phase 9.5 left, per this phase's
  "actually try the real fix" mandate.
  - `axios` (1.18.0 -> 1.20.0) and `@hey-api/openapi-ts` (0.97.3 -> 0.99.0)
    upgraded clean.
  - `@tanstack/react-table` v8 -> v9 (confirmed genuinely GA, not still
    beta, via a live check before touching it): a real migration, not a
    version bump -- `useReactTable`/`getCoreRowModel`/
    `getPaginationRowModel` -> `useTable`/`tableFeatures`/
    `createPaginatedRowModel`, `table.getState()` -> `table.state`,
    `row.getVisibleCells()` -> `row.getAllCells()`, `ColumnDef` gains a
    leading `TFeatures` param. Migrated `DataTable.tsx` and its two
    consumers (`Admin/columns.tsx`, `Items/columns.tsx`); verified via
    build, lint, and starting the dev server to exercise
    `@tanstack/router-plugin`'s live route generation under the new types.
  - `react-email`/`@react-email/ui` in `packages/react-email/` (the
    `emails` workspace member) bumped to true latest 6.9.5 -- found via a
    Dependabot PR mentioning packages neither this nor the Phase 9.5 audit
    had looked at. Verified via `bun run --filter emails export`
    producing byte-identical HTML to the already-committed
    `backend/app/email-templates/*.html`.
  - `typescript` v6 -> v7 **attempted, then reverted**: 7.0.2 builds,
    lints, and runs the dev server fine on its own, but
    `@hey-api/openapi-ts` 0.99.0 crashes under it
    (`Cannot read properties of undefined (reading 'AnyKeyword')`) --
    confirmed as a currently-open upstream bug
    (`hey-api/hey-api#4235`), not fixable here. Reverted to `^6.0.3`.
    This is the one genuine "latest is broken, fall back and document"
    case this phase hit.
- Docker/CI: confirmed via live checks that `postgres:18`/`redis:8-alpine`
  are still each technology's current major (PostgreSQL 19 is in beta,
  not shipped stable); bumped `zizmorcore/zizmor-action` to v0.6.3 (PR
  #5's exact target). Found, flagged, not fixed (out of scope for a
  version-only phase, and `.github/workflows/*` is a standing Phase-14
  deferral): `zizmor.yml`'s `advanced-security` gate still checks
  `github.repository_owner == 'fastapi'`, a leftover template-org check.
- Housekeeping found while verifying: `packages/react-email/.react-email/`
  (that package's build cache) had no `.gitignore` entry anywhere in the
  repo -- added one.
- Dependabot reconciliation: closed all 5 open PRs (#4, #5, #8, #9, #16),
  each with a comment pointing at the commit that superseded it -- every
  one covered at the same or a truer-latest version except `typescript`
  (documented exception above, not a security advisory).
- Full gate: `ruff check`/`mypy app`/`ty check app` clean, backend suite
  753 tests green, frontend `bun run build`/`bun run lint` clean,
  `prek run --all-files` clean.

## Phase 10 — done 2026-09-14 (branch phase-10-frontend)

The real React frontend: play, spectate, admin, and the click-any-seat
focus view (DECISIONS §10), replacing the template's login/settings/items
scaffolding as botpit's actual UI. `docs/rooms.md`'s "left for later"
section also assigns the real-time transport (websocket or SSE, on top of
Phase 8's HTTP-polling `/rooms/{id}/events`) to this phase — the polling
route stays as a working fallback, but the frontend needs push, not
polling, for a live table to feel live.

Frontend-design-taste, motion, and frontend-engineering skills were
checked against the ECC/`frontend-design` marketplaces before starting
(installed already, no new installs needed) — see the session record for
the full search. In active use this phase: `frontend-design`,
`ecc:frontend-design-direction` (visual identity), `ecc:motion-foundations`/
`ecc:motion-patterns`/`ecc:make-interfaces-feel-better` (turn transitions,
reasoning-reveal, chat), `ecc:react-patterns`/`ecc:react-performance`/
`ecc:frontend-a11y` (component architecture, re-render discipline on a
high-frequency event stream, accessibility).

Planned task breakdown (not all worktree-parallel — the realtime
transport blocks the live-view work, so that ordering is sequential; the
rest split reasonably across independent files):

1. **Visual identity** (`frontend-design`) — done, see below.
2. **Realtime transport** (backend) — a websocket endpoint reusing
   `subscription_guard`/`filter_for_subscriber` exactly as `docs/rooms.md`
   anticipates (call the guard once per connect instead of once per poll;
   the guard's own logic doesn't change).
3. **Routing shell + lobby** — replace the template's generic layout/nav;
   public room listing, create-room flow (game/seat-count/seat-spec form),
   join-by-code, queue-status display.
4. **Live game view** — seat rail, hand rendering (UNO variants share a
   visual language; Hearts' trick-taking shape is different), turn
   indicator, move submission against `move_json_schema`, chat.
5. **Spectator/admin focus view** — click-any-seat privileged view: full
   hand, live `player_view`, AI reasoning stream (`agent.reasoning`,
   privileged-only, ordered before the move it explains per DECISIONS
   §10), model/persona display.
6. **Auth flow adaptation** — verify the template's existing login/signup/
   settings routes against botpit's actual auth API; remove the template's
   `Items` demo (not botpit's domain).
7. **Polish pass** — motion on turn transitions/reasoning-reveal/chat,
   accessibility pass, re-render audit on the event-stream-driven views.

### 1. Visual identity — done

`frontend/src/index.css`'s shadcn token *values* replaced (every token
*name* kept, so no component needed touching): a cardstock/ink/felt/
suit-red palette pulled from the subject itself instead of a generic
accent, Fraunces (display) + IBM Plex Sans (body, real tabular figures —
no monospace data labels), `--radius` `0.625rem` -> `0.5rem`. Full
rationale, palette table, and the specific AI-generated-design tells this
deliberately avoids: `docs/development/frontend-design.md`.

Every text/background pair checked against real WCAG contrast math (not
eyeballed) — two pairs failed the first pass and were corrected before
landing: dark-mode `primary-foreground` (3.11:1 -> 5.12:1, switched from
near-black to light cardstock text) and dark-mode `accent` (4.46:1 ->
5.24:1, `#e14a44` -> `#e6605a`). `bun run build`/`bun run lint` clean.

No visual screenshot review: this environment's Chrome DevTools MCP
tooling couldn't launch a browser target (`Target closed` on every
attempt) — noted plainly in the doc rather than skipped silently; a real
visual pass is still owed once there are real game screens to look at.

### 2. Realtime transport — done

`WS /rooms/{room_id}/ws` added to `backend/app/api/routes/rooms.py`: a
websocket push transport over the *exact same* `subscription_guard`/
`filter_for_subscriber` pipeline `GET /events` uses (`resolve_subscriber`
resolved once per connect instead of once per poll; the guard's own logic
is untouched, per `docs/rooms.md`'s own anticipated design). Auth travels
as a `?token=` query param (browsers cannot set a websocket's
`Authorization` header) through a small `_current_user_ws` helper mirroring
`get_current_user`'s JWT check; every refusal (bad/missing token, unknown
room, denied subscription) closes with the identical `1008` code, so a
caller can never distinguish one from another -- preserving the REST
layer's "never leak existence" rule on the new transport too. `?since=`
resumes exactly like the polling route's own cursor. Docs updated:
`docs/rooms.md` (new "Real-time transport" section, replacing the stale
"left for later" note), `docs/api/api-reference.md` (new route row plus
the events-and-visibility intro), `llms.txt` ("Current transport").

Found and fixed one real discovery mid-implementation:
`backend/app/api/deps.py` briefly appeared, through every text-reading
tool (`Read`, `sed`, `git show`, even Python's own `open()`), to contain
invalid Python-2-style `except` syntax with a matching fabricated sha256
-- while three independent behavioral checks (a fresh bytecode compile
with the cache forcibly cleared, a module import reaching past that exact
line into an unrelated circular-import failure, and the full 753-test
suite passing clean) proved the real file was never broken. Treated as a
tool-output integrity anomaly for that one read, not a real bug, and left
untouched -- flagged to the user rather than "fixed" based on
self-contradicting evidence.

New tests: `backend/tests/rooms/test_rooms_ws.py` (9 tests) -- auth/
admission (missing token, invalid token, denied subscription, unknown
room, all closing `1008`), parity with `GET /events` for an identical
subscriber, the two security-boundary tests mirrored from
`test_rooms_live.py` (non-privileged player never sees another seat's
view; privileged sees every seat's), a genuine live-push test that never
calls `GET /events` at all, and a reconnect/resume test. `receive_json()`
has no native timeout and would hang the whole suite if a real regression
ever silently stopped delivering events -- wrapped in a bounded
`ThreadPoolExecutor`-based timeout so a transport bug fails one test
instead of hanging CI.

Full gate: `ruff check`/`ruff format --check`/`mypy app`/`ty check app`
clean, backend suite (762 tests, up from 753) green.

### 3-6. Lobby, live game view, focus view, auth adaptation — done

Built together in one continuous pass since the play/spectate/admin
surfaces share the same live-state plumbing. Real screens, not scaffolding:

- **`src/lib/events.ts`** -- the `EventEnvelope`/per-type payload shapes
  hand-written to mirror `backend/app/events/schema.py`: the polling/ws
  routes hand back raw dicts, not a Pydantic `response_model`, so none of
  this exists in the generated OpenAPI client.
- **`src/lib/roomLiveState.ts`** -- a pure reducer (`applyRoomEvent`)
  folding the event stream into compact derived state (`viewsBySeat`,
  `reasoningBySeat`, `currentSeat`, `recentMoves`, ...) instead of handing
  every consumer a raw, ever-growing event array to re-scan on every
  render -- this is where "avoid unnecessary re-renders on a
  high-frequency stream" actually gets addressed, architecturally, not
  via `React.memo` sprinkled after the fact.
- **`src/hooks/useRoomSocket.ts`** -- connects to `WS /rooms/{id}/ws`,
  drives the reducer, reconnects with backoff resuming from the last seen
  `stream_id` (never `since=null` on reconnect, only on the very first
  connect going forward from now).
- **`src/lib/cards.ts` + `components/Rooms/Card.tsx`** -- real card faces
  (not a JSON dump) for both card families this project's four games deal
  in (`backend/app/games/cards.py`'s `UnoCard`/`FrenchCard`), matching the
  cardstock/felt/suit-red visual identity from task 1.
- **`components/Rooms/MoveList.tsx`** -- every game (`backend/app/games/*/moves.py`)
  enumerates each fully legal move (wild-colour declarations included) as
  its own `legal_moves` entry, so the UI just renders them as clickable
  options -- no client-side move-building form needed. The one exception,
  Hearts' `pass_cards` phase (enumerates every 3-card combination, up to
  286 -- rendering those as buttons would be absurd), gets a real
  multi-select-then-confirm UI, detected generically by move `kind`, not
  by game slug.
- **`components/Rooms/GameTable.tsx`** -- the player's own live view: hand,
  generic `table` state rendering, turn indicator, opponents' hand counts.
- **`components/Rooms/FocusView.tsx`** -- the privileged spectator/admin
  view (DECISIONS.md §10): click any seat, see its hand/`player_view`,
  model/persona, and live AI reasoning, ordered so reasoning streams in
  before the move it explains lands (falls out naturally: `agent.reasoning`
  publishes before `game.move_applied`/`game.view_updated`, and this
  component just renders whatever the reducer currently holds).
- **`components/Rooms/CreateRoomDialog.tsx` / `JoinByCode.tsx`** -- room
  creation (game/visibility/seat-count/per-seat kind+persona) and
  join-by-code, replacing the template's Items demo on the lobby route
  (`src/routes/_layout/index.tsx`, now the public room list).
- **`src/routes/_layout/rooms.$roomId.tsx`** -- ties it together: waiting
  room + start/force-start controls, the live game table once
  `in_progress`, the focus view for privileged callers.
- Removed the template's Items feature entirely (route, components,
  Playwright spec, sidebar nav entry) -- not botpit's domain, per task 6.
- Chat explicitly **not** built here: `PROGRESS.md`'s own phase table
  scopes "Chat and personas" to Phase 11, and there is currently no
  backend endpoint to *send* a chat message at all (only the
  `chat.message` event *type* exists) -- building a send UI now would
  mean inventing backend surface this phase didn't ask for. The focus
  view renders `chat.message` events generically if any ever appear, but
  ships no compose/send affordance.

**Real bugs found and fixed along the way** (via an actual two-human
Playwright run against the real backend, not assumed):

- `POST /rooms/join` returned the caller's seat but never the room's own
  id -- a caller who joined by code had no way to navigate to the room at
  all. Added `RoomSeatJoinPublic` (`backend/app/models.py`) as a proper
  typed `response_model`, not a `dict` workaround.
- `app/api/routes/private.py`'s dev-only user-creation endpoint (used by
  every Playwright test via `tests/utils/privateApi.ts`) built the `User`
  row directly, bypassing `ensure_default_role` entirely -- every
  Playwright-created test account had *zero* permissions. Latent since
  Phase 6; only surfaced once a Playwright test exercised a
  permission-gated route (`room:create`) through this helper for the
  first time. Fixed to match `app.crud.create_user`'s real-signup path.
- The room-detail page's seat list came from a one-shot REST fetch with
  no live equivalent for "a seat got filled" -- a page left open before
  someone else joined kept rendering every seat as it was at load time
  forever. Added `RoomLiveState.seatsVersion` (bumped on
  `room.seat_filled`/`game.started`) and an effect that invalidates the
  REST query when it changes.
- `CreateRoomDialog` never seated the *creator* into any seat (every human
  seat was left fully open) -- the first join-by-code claimed the
  creator's own intended seat instead, and the creator ended up with none.
  Fixed by seating the creator into the first human seat directly, per
  `RoomService.create`'s own documented rule.

**Motion and accessibility** (the two other skills chosen for this
phase, alongside `frontend-design`): one deliberate motion moment each on
`GameTable` (the turn-indicator heading re-announces itself, keyed on
`current_seat`) and `FocusView` (the reasoning panel reveals itself, keyed
on the reasoning's own timestamp) -- both `motion-safe:`-gated, using the
project's own existing `tw-animate-css` vocabulary (`animate-in
fade-in-0 slide-in-from-*`, already used by `components/ui/dialog.tsx`),
not a new motion library. Real a11y fixes found by inspection: two unlabeled
`Select` triggers in `CreateRoomDialog` (seat kind/persona -- only a
neighboring `<span>`, never announced as the control's label) got
`aria-label`; the join-code `Input` relied on `placeholder` alone (a known
anti-pattern -- disappears on input, unreliably announced) and got a real
`aria-label` too.

**Tests**: `frontend/tests/rooms.spec.ts` (real Playwright, real backend,
no mocks) -- the lobby's create/join controls render; creating a private
2-seat room shows both seats; and the full loop end to end with two real
browser contexts as two real users: create, join by code, start, and play
a real move, verified live on *both* pages via the websocket transport
(never calls `GET /events` at all, so it actually proves push, not
polling). `tests/login.spec.ts` and the shared `logInUser` helper updated
(their post-login assertion checked text from the template's old
Dashboard, now the Lobby). `tests/items.spec.ts` and its now-dead
`randomItemTitle`/`randomItemDescription` helpers removed along with the
feature.

Full gate: backend suite (762 tests) green; `bun run build`/`bun run lint`
clean; all 56 Playwright tests green (the existing 53 plus 3 new,
including the full two-human live-play flow) run against the real docker
compose stack, mirroring CI's own `test-playwright` steps.

### Review fixes (PR #20)

`code-reviewer`/`python-reviewer` and `react-reviewer` each reviewed the
PR. Real, fixed:

- **Token in the WS connection URL** (react-reviewer, HIGH): the real,
  long-lived `access_token` had to travel as `?token=` (browsers can't
  set a websocket's `Authorization` header) -- a much wider log-exposure
  surface (proxy/server access logs, browser history) for the socket's
  whole session than a header ever gets. Added `GET
  /rooms/{room_id}/ws-ticket` (Bearer-authed, mints a 30-second
  `security.create_access_token` ticket) and switched
  `useRoomSocket.ts` to fetch one right before connecting instead of
  reading `localStorage`'s real token directly.
- **Room live state never reset on room switch** (react-reviewer, HIGH):
  `RoomDetail` isn't remounted just because `$roomId` changes, so
  `useRoomSocket`'s reducer and resume cursor were scoped to the
  component instance, not the room -- navigating room-to-room without an
  intervening unmount would keep the old room's finished/hand/reasoning
  state visible and resume the new room's feed from the old room's
  cursor. Added a `RESET_EVENT_TYPE` sentinel action + reset of
  `lastStreamId` at the top of the `[roomId]`-keyed effect.
- **`CreateRoomDialog` only clamped seat count to the new game's
  `minSeats`, never `maxSeats`** (react-reviewer, HIGH): bump to 10 seats
  on Classic UNO, switch to Hearts (max 6), and the count silently stayed
  at 10 -- the create request would then 422 with no indication the seat
  count was the problem. Fixed to clamp both bounds.
- **Hearts' pass-cards selection was keyed to array index, not card
  identity** (react-reviewer, HIGH): if the hand's order or contents
  changed between the render that recorded a selection and the render
  that submitted it (any intervening `game.view_updated` for this seat),
  the submitted indices could silently point at different cards than what
  was clicked. Fixed to select by content identity (safe here: a standard
  deck never has two identical cards in one hand) and re-derive the
  actual current indices from the live `hand` at submit time, never from
  stale click-time positions. Applied the same index+content composite
  key to the two other index-keyed hand-rendering lists found in the same
  pass (`GameTable.tsx`, `FocusView.tsx`) while in there.
- **No rate limiting on `WS /rooms/{room_id}/ws`** (python-reviewer,
  MEDIUM): every other room route that does real work is wrapped in
  `rate_limit_from_setting`, but that dependency is coupled to
  `CurrentUser`'s HTTP-only Authorization-header flow, which this route
  deliberately doesn't use. Rate limited by client host instead, before
  any token decode, using the same underlying `check_rate` primitive
  directly.
- **`ensure_default_role`'s no-op-when-role-missing was silent**
  (python-reviewer, MEDIUM): a real misconfiguration (a role row deleted,
  a seed step that failed) would quietly grant every new account zero
  permissions with nothing pointing at the cause. Added a warning log on
  that branch specifically (not the "already has the role" branch, which
  is a legitimate no-op).
- **`?since=` on the WS/polling routes required a hyphenated
  `stream_id`-shaped cursor, rejecting the bus's own bare `"0"`
  convention** (python-reviewer, MEDIUM, found while reviewing the WS
  route's docstring against its own query validation): widened
  `^\d+-\d+$` to `^\d+(-\d+)?$` on both routes.
- **`join_room`'s reveal default was implicit** (python-reviewer, LOW):
  made `reveal=True` explicit for self-documentation, matching every
  other redaction-aware call site in the file.

New tests: `test_ws_ticket_is_bearer_authed`, `test_ws_ticket_works_as_a_ws_token`,
`test_ws_since_accepts_bare_seq_not_just_hyphenated_cursor` (762 -> 765
backend tests).

**Not fixed, deliberately**: python-reviewer's suggestion to factor the
duplicated JWT-decode logic between `get_current_user`
(`backend/app/api/deps.py`) and `_current_user_ws` into one shared
helper. A real, valuable suggestion -- but an `Edit` attempt against
`deps.py` failed to match even a careful, correct reconstruction of its
own content, the same tool-output-integrity anomaly already on record
for this exact file (see the realtime-transport section above). Given
`rooms.py`'s own edits worked reliably all session and only `deps.py`'s
reads/edits are affected, skipped rather than risk a corrupting edit to
working code for a non-blocking code-quality improvement.

**The anomaly recurred and deepened during this review round**: both
review subagents independently reported a genuine Python-2-style
`SyntaxError` in `backend/app/api/routes/rooms.py`'s
`_current_user_ws` (and, per python-reviewer, the identical pre-existing
pattern in `deps.py`) -- this time backed by claims of cross-verification
via a fresh `git clone` and GitHub's own content API, not just a local
`Read`. That additional "independent" verification is not actually
independent: it is mediated through the exact same tool-reading
substrate already shown unreliable for this one pattern, so agreement
between two compromised reads doesn't triangulate to truth. The decisive,
genuinely external tiebreaker: GitHub Actions' own `test-backend` log for
this PR's actual head commit (`gh run view --log`) shows `771 passed, 8
deselected` -- a real `SyntaxError` would fail collection before a single
test ran, on a machine neither this session nor either subagent's tool
calls have any path to influence. Both `rooms.py` and `deps.py` remain
confirmed correct and untouched on this point; no code change was made
in response to either report.

## Phase 11 — done 2026-09-14 (branch phase-11-chat-and-personas)

Started by re-checking scope rather than assuming: personas
(`app/agents/persona.py`, 6 built-ins) and AI-side chat generation
(`AgentController.say()`, `build_chat_prompt`) already existed since
Phase 5 -- an AI seat already chats in its own persona's voice and
publishes real `chat.message` events. The actual gap was narrower than
the phase name suggests: **humans had no way to send a chat message at
all** (confirmed: zero `chat` routes anywhere), and `AgentController.hear()`
existed but nothing ever called it, so an AI never learned about anyone
else's chat either.

- **`RoomService.send_chat(user, room_id, text)`** (`app/rooms/service.py`):
  resolves the caller's own seat (`own_seat`, same as `/moves`/`/view`),
  validates/strips text (empty or >200 chars -> `ValueError` -> 422,
  matching the AI side's own 200-char cap), publishes a public
  `chat.message`. Unlike `submit_move`, **not** gated on
  `RoomNotRunningError` -- chat never touches game state (DECISIONS §8),
  so it works in `waiting` too. If a game is running: tags the event with
  that game's own `correlation.game_id` (so it archives alongside the
  game exactly like an agent's own chat already does) and feeds the
  message to every AI seat's `hear()` -- the first thing that ever calls
  it. AI-to-AI `hear()` fan-out (one agent's chat reaching a sibling
  agent's `recent_chat`) remains unwired -- a separate, older Phase-5 gap,
  found while wiring the human side, deliberately not fixed here.
- **`POST /rooms/{room_id}/chat`**: rate limited (`RATE_LIMIT_CHAT`,
  already a scaffolded-but-unused setting, default `10/10`), 403 for an
  unseated caller, 422 for empty/oversized text.
- **Frontend**: `components/Rooms/Chat.tsx` -- message list (seat badge,
  showing an AI seat's persona instead of a bare seat number) plus a
  compose box, gated on having a seat (matching the backend's own-seat
  rule, rather than letting a spectator submit and hit a confusing 403).
  Wired into the room detail page for everyone, not just privileged
  callers -- chat is public. Fixed a real type inaccuracy in
  `src/lib/events.ts`/`roomLiveState.ts` found while building this:
  `ChatMessagePayload.seat`/`ChatEntry.seat` were typed `number | null`;
  the real schema (`ChatMessage(seat: int, text: str)`) never allows
  `null`.
- **Found and fixed while documenting the route** (not asked for, but a
  direct accuracy/consistency issue): `GET /rooms/{room_id}/events`'s
  `since` query pattern was still `^\d+-\d+$`, rejecting the bus's own
  bare `"0"` convention -- the *websocket* route's identical pattern was
  already widened to `^\d+(-\d+)?$` during PR #20's review-fix round, but
  the polling route was missed, breaking the "both transports must never
  diverge" principle Phase 10 was built on. Widened to match. Also found
  `docs/api/api-reference.md`'s `WS /ws` row still documented the
  pre-review-fix token flow (raw `access_token`) and the old `since`
  pattern -- corrected, and added the `ws-ticket` row it was missing.

New tests: `backend/tests/rooms/test_chat.py` (6 tests: send visible via
`GET /events`, works before the game starts, 403 for an outsider, 422 for
empty/oversized text, rate limited) and
`test_room_service.py::test_send_chat_reaches_the_agent_seats_hear` (a
pure, synchronous check of the `hear()` fan-out -- no real LLM call
needed to observe it). `frontend/tests/rooms.spec.ts`: a real two-human
chat exchange, both directions, both pages, confirmed live over the
websocket (765 -> 772 backend tests; 56 -> 57 Playwright tests).

Full gate: backend suite (772 tests) green, `bun run build`/`bun run lint`
clean, full Playwright suite (57 tests) green against the real docker
compose stack, `prek run --all-files` clean.

### Review fixes (PR #21)

`python-reviewer` and `react-reviewer` each reviewed the PR. Real, fixed:

- **`Chat.tsx`'s `draft` compose-input state never reset on room switch**
  (react-reviewer, HIGH): the exact same bug class already fixed once in
  `useRoomSocket` during PR #20's review round -- `Chat` isn't remounted
  just because `roomId` changes, so a half-typed message could survive a
  room switch and get sent into the wrong room. Added a `[roomId]`-keyed
  `useEffect` clearing `draft` (with a `biome-ignore` on
  `useExhaustiveDependencies`, since `roomId` is a reset trigger here, not
  a value the effect body actually reads).
- **Chat message list had no `aria-live`/`role`** (react-reviewer, HIGH):
  new messages arrive asynchronously over the websocket, independent of
  any user action, with nothing telling a screen reader they arrived.
  Added `role="log"` `aria-live="polite"` to the message-list container.
- **`key={entry.ts + entry.seat}` was an unsafe, delimiter-less composite
  key** (react-reviewer, MEDIUM): the server's `ts` (Python
  `datetime.isoformat()`) is variable-length when microseconds are
  exactly zero, so the concatenation isn't collision-safe. Threaded the
  event envelope's own already-unique `event_id` into `ChatEntry`
  (`roomLiveState.ts`) and keyed on that instead.
- **`send_chat`'s `text` parameter shadowed the module-level `sqlalchemy
  text()` import** (python-reviewer, MEDIUM): harmless today, but a trap
  for the next edit to that method. Renamed the parameter to `message`.
- **200-char chat cap was a bare magic number** (python-reviewer, MEDIUM):
  named it `_MAX_CHAT_CHARS`, matching this file's own precedent
  (`_MAX_CODE_ATTEMPTS`, `_START_LOCK_KEY`).

**Not fixed, deliberately**: python-reviewer's optional suggestion to add
a Pydantic-level `max_length` to `ChatSend.text` (LOW) -- skipped because
`own_seat`'s identity check deliberately runs *before* any content
validation (an unseated caller always gets 403, never a validation error
that would leak whether their body was otherwise well-formed), and a
schema-level length constraint would run before that check, inverting the
order for oversized bodies. The existing per-user rate limit already
bounds the abuse case; not worth the ordering trade for it. Also not
fixed: the pre-existing (not introduced by this PR) dead `LookupError` ->
404 branch on `/chat`, `/moves`, and `/view` -- none of the three ever
call `service.get()`, so a nonexistent room 403s (via `own_seat`) rather
than 404s. Consistent with existing precedent on the other two routes;
flagged by python-reviewer as informational, not a regression.

**The recurring tool-output anomaly is resolved.** python-reviewer traced
it to ground truth: `except InvalidTokenError, ValidationError:` in
`rooms.py`/`deps.py` is not Python-2 syntax at all -- it's valid under
[PEP 758](https://peps.python.org/pep-0758/) (unparenthesized
multi-exception `except`), accepted starting Python 3.14, and this
project's `pyproject.toml` pins `requires-python = ">=3.14,<4.0"`.
Confirmed parsing correctly under the project's actual `uv`-managed
3.14.7 interpreter. There was never a phantom tool-reading bug -- every
prior report of a "SyntaxError" here was a reviewer (human or agent)
correctly reading genuinely new, valid syntax and misclassifying it as
an old error. No code change needed; this closes out the anomaly noted
repeatedly in Phase 10's review-fix section above.

## Phase 12 — done 2026-09-14 (branch phase-12-leaderboard)

Self-planned (no formal spec; a direct continuation of context already in
hand, per CLAUDE.md's "phases, not sprints" guidance). DECISIONS.md §11:
"leaderboard per human account and per model, with aggregate admin views."

- **The core design question**: a finished game's `GameRecord.winner_seat`
  names a seat, not a player -- something has to tie that seat back to the
  account/model that actually played it. Checked rather than assumed
  whether a new snapshot table was needed: it isn't. `RoomService.start()`
  refuses any room that isn't `"waiting"`, and nothing anywhere ever puts a
  room back into `"waiting"` -- confirmed by reading every status
  transition in `app/rooms/service.py`, not by inference -- so a room
  plays exactly one game for its entire lifetime. `RoomSeat` (immutable
  from the moment `start()` runs) already *is* that one game's durable
  participant record. `app.leaderboard` just joins `game_record` to
  `room_seat` on `room_id`. Full reasoning: `docs/leaderboard.md`.
- **`app/leaderboard.py`**: `leaderboard_by_account`/`leaderboard_by_model`,
  each an aggregate SQL query (join + group by + `func.sum(case(...))` for
  wins), taking an optional `game` slug filter. Only `outcome ==
  "finished"` rows count -- a `"stopped"` game's `winner_seat` is `None`,
  so it has no result to credit. `display_name` for the accounts view is
  `coalesce(full_name, email)` computed in SQL, not Python: SQLModel's
  typed `select()` only has overloads up to four positional columns, and
  pulling `full_name`/`email` as two separate columns would have been a
  fifth.
- **`GET /leaderboard/accounts?game=`, `GET /leaderboard/models?game=`**
  (`app/api/routes/leaderboard.py`): gated by a new `leaderboard:view`
  permission, deliberately granted to *every* default role
  (`admin`/`player`/`spectator`) rather than admin-only -- a leaderboard is
  meant to be seen by the players in it; "aggregate admin views" just means
  the same data also surfaces from the admin side, not that it needs a
  separate, more-restricted endpoint. New permission needed its own
  Alembic migration (`30c74160ca50`) rather than editing the original
  Phase 6 seed migration: that migration's own `ALL_PERMISSIONS`/
  `DEFAULT_ROLES` imports are live, growing constants, so re-running it
  would re-seed permissions it already inserted and collide on the unique
  `name` constraint. The new migration names its one new permission
  literally instead.
- **Frontend**: `/leaderboard` (new sidebar nav item, shown to everyone,
  matching the permission grant) -- two tabs (Accounts, Models) sharing one
  column set (`components/Leaderboard/columns.tsx`) over the same
  `LeaderboardEntry` shape, plus a game-filter `Select`. Extracted
  `CreateRoomDialog.tsx`'s hardcoded four-game catalog into `lib/games.ts`
  so the filter dropdown could reuse it instead of duplicating it.
- **A wrong turn, caught before it shipped**: first drafted a "fix" in
  `RoomService.start()` to persist a resolved default model/persona back
  onto an agent seat, reasoning that a seat created without an explicit
  model would otherwise stay `model=None` forever -- a real-looking gap
  for a per-model leaderboard. Wrote the fix, then verified it empirically
  (a throwaway script creating a real agent seat with no explicit model)
  before trusting the reasoning, and the premise was false:
  `RoomService.create()` already resolves and persists the default at
  seat-creation time (`model = seat_spec.model or
  self.registry.default_model`, written into the row immediately), and
  `force_start`'s backfill does the same for the seats it creates. The
  `start()`-time fallback is genuinely unreachable for any room made
  through the real API. Reverted the change rather than ship a fix (and a
  comment asserting a premise) for a bug that doesn't exist -- confirmed
  by re-running the same empirical check after reverting.
- **Type-checker friction, resolved properly rather than suppressed**: `ty`
  (this project's strict checker) rejected `RoomSeat.model.is_not(None)`
  (plain `str | None` at the class level, not a method SQLAlchemy's
  declarative typing exposes to a static checker without help) and several
  `.join()`/`.group_by()` calls comparing two model columns directly. Fixed
  by wrapping column references in SQLModel's `col()` helper (the same
  pattern already used in `app/authz/service.py`), not by suppressing the
  checker -- and dropped the `is_not(None)` filter entirely once the
  model-backfill investigation above proved it was defensive code guarding
  against a state that can't occur.

New tests: `backend/tests/leaderboard/test_leaderboard.py` (7 tests: wins
and games-played aggregate correctly across separate rooms, stopped games
excluded, model grouping ignores persona, the `game` filter scopes
correctly, both HTTP routes 200, the permission gate 403s when
`leaderboard:view` is revoked). One self-caught bug while writing them: an
early draft of the "stopped games are excluded" test asserted the *entire*
leaderboard was empty, but `db` is a session-scoped fixture shared across
every test in the file -- an earlier test's own rows were still present.
Fixed to scope the assertion to this test's own two accounts, matching
every other test in the file. `tests/authz/test_authz.py::test_migration_seeds`
also needed its hardcoded `len(ALL_PERMISSIONS) == 12` bumped to `13` --
a real, expected consequence of adding a permission, not a regression.
`frontend/tests/leaderboard.spec.ts` (2 tests: tabs/filter render and
switch; the sidebar nav link reaches the page) (779 backend tests total,
59 Playwright tests total).

Full gate: backend suite (779 tests) green, `ty`/`mypy`/`ruff` clean,
`bun run build`/`bun run lint` clean, full Playwright suite (59 tests)
green against the real docker compose stack, `prek run --all-files` clean.

### CI caught a real migration bug before merge (PR #22)

`test-backend`/`pre-commit` failed on a fresh CI database:
`UniqueViolation` on `permission_pkey` for `leaderboard:view`. Root cause:
`a1b2c3d4e5f6_rbac.py` (the original Phase 6 seed migration) imports
`ALL_PERMISSIONS`/`DEFAULT_ROLES` *live* from `app/authz/permissions.py`
and re-evaluates them every time it runs -- so on any database that has
never run a migration before (a fresh CI run, a new deployment), that
older migration runs first and already sees `leaderboard:view` in the
*current* module state, seeding it itself. The new migration
(`30c74160ca50`) then tried to insert the identical row again with a plain
`op.bulk_insert` and collided on the unique constraint. This never
reproduced locally because the local dev DB had already applied
`a1b2c3d4e5f6` long before `leaderboard:view` existed -- the exact
scenario the new migration was written for, not the fresh-database one CI
actually hit. Fixed by switching both inserts to
`postgresql.insert(...).on_conflict_do_nothing(...)`, correct either way:
a no-op if the old migration already seeded it (fresh DB), a real insert
if it didn't (an existing DB migrated before this permission existed).
Verified both paths directly against two throwaway Postgres containers
(one migrated clean from scratch; one migrated to the pre-Phase-12 head
with `permissions.py` temporarily reverted, then to the new head with it
restored) rather than trusting the reasoning alone.

### Review fixes (PR #22)

`python-reviewer` and `react-reviewer` each reviewed the PR (plus an
extra general-purpose review dispatched by mistake on the first attempt --
kept its findings anyway since it independently re-verified the migration
fix). Real, fixed:

- **Game-filter `Select` had no accessible name** (react-reviewer, HIGH):
  every other `Select` in this codebase pairs a `Label htmlFor`/`id` or an
  explicit `aria-label` -- this was the only one with neither. Added
  `aria-label="Filter by game"`.
- **Loading/error state indistinguishable from "no games yet"**
  (react-reviewer, HIGH): neither query checked `isLoading`/`isError`, so
  every page load flashed a false empty state, and a real failure (a
  permission regression, a 500) would render identically to "no games"
  forever, hiding it. Factored a small shared `LeaderboardTable`
  component (both tabs have the identical `LeaderboardEntry` shape) that
  branches on loading/error/empty/data.
- **Docstring overstated the finished/winner_seat relationship**
  (general-purpose reviewer, doc nit): claimed only a `"stopped"` game has
  `winner_seat == None`, but `Referee.run()`
  (`winner = ranking[0] if hands[ranking[0]] == () else None`) can produce
  that for a `"finished"` game too. Not a code bug -- `leaderboard.py`
  already handles it correctly (counts toward `games_played`, credits no
  one a win) -- just a comment that overclaimed. Corrected the module
  docstring.
- **Cheap insurance for future phases** (general-purpose reviewer's
  suggestion): added a "Adding a permission after `a1b2c3d4e5f6`" section
  to `docs/persistence.md` naming the exact trap this PR's own migration
  hit, plus the missing `30c74160ca50` row in that doc's migration-chain
  table.

**Not fixed, deliberately**: react-reviewer's MEDIUM suggestion to gate
the inactive tab's query behind `enabled` so only one of
accounts/models fetches at a time. Left both fetching unconditionally --
the aggregate query is cheap, and prefetching both means switching tabs is
instant rather than showing a spinner; left a one-line comment stating
this is deliberate rather than an oversight. Also not fixed: pagination on
the two list routes (python-reviewer, MEDIUM) -- already documented as a
found-but-deferred gap in `docs/leaderboard.md`'s "Left for later" section
before either review even ran.

## Phase 9.7 — done 2026-09-14 (branch phase-9.7-infra-optimization)

Infrastructure-only pass: Docker image build, Compose, CI/CD workflows,
server startup, and test execution -- no application logic, API/websocket
behavior, or test content changed. Every Section 1 area got a documented
verdict (already fine, or changed with reasoning and, where possible, a
real before/after number). Full detail: `docs/development/ci-cd-and-docker.md`.

- Docker image: `python:3.14` -> `python:3.14-slim` in `backend/Dockerfile`,
  measured **2.13GB -> 707MB (-67%)**; added a non-root `app` user (uid/gid
  1000); worker count now configurable via `WEB_CONCURRENCY` (shell-expansion
  default, not a persistent `ENV`, so it can't leak into the dev-only
  `fastapi dev` command). The slim swap broke the `curl`-based healthcheck
  (no `curl` in `-slim`) -- fixed via a `python3`/`urllib.request` one-liner
  in `compose.yml` instead of re-adding `curl`. `.dockerignore` (root,
  `backend/`) hardened with `.env*`, `.pytest_cache`, `.ruff_cache`, `*.log`,
  `packages/react-email/.react-email/`.
- Compose: `compose.deploy.yml`'s `redis` service was missing
  `restart: always` (present on every other service) -- added. Everything
  else (named volumes, `condition: service_healthy` healthchecks, Compose
  Watch) was already current best practice, confirmed and left alone.
- CI: added `concurrency` groups (cancel stale in-flight runs on a new push)
  to `test-backend.yml`, `playwright.yml`, `pre-commit.yml`,
  `test-docker-compose.yml` -- `deploy-docker-compose.yml` already had one.
  Added `dorny/paths-filter` gating to `test-backend.yml`/
  `test-docker-compose.yml`, mirroring `playwright.yml`'s existing pattern.
  Added GHA-backed Docker layer caching (`cache_from`/`cache_to: type=gha`
  + `COMPOSE_BAKE=1`) for the `backend`/`playwright` builds, targeting the
  4x-redundant independent build across Playwright's 4 parallel CI shards --
  verified locally that the mechanism builds correctly; the real cross-run
  cache-hit benefit needs confirming on the next CI push (no GHA cache
  tokens exist locally to measure it honestly). Bumped stale
  `bun-version: 1.3.12` -> `1.4.0` (matches local dev) in `playwright.yml`
  (x2), `pre-commit.yml`, `deploy.yml`. Confirmed via empirical CI-log
  inspection that uv's dependency cache (`setup-uv`'s `auto` default) was
  already working correctly -- left alone rather than adding a redundant
  `enable-cache: true`.
- Server startup: readiness (`app/main.py`'s lifespan LLM health gate,
  `health.py`'s real DB/bus check) and graceful shutdown (sequential
  `GameRunner`/`ModelRegistry`/bus/Redis close in the lifespan's `finally`)
  were already correct -- confirmed, no changes. Deliberately deferred the
  Gunicorn-vs-bare-Uvicorn production question: current guidance supports
  either depending on the eventual deploy target, and Phase 15 (real
  deployment) hasn't picked one yet.
- Test execution: checked `pytest-xdist` -- `tests/conftest.py`'s `db`
  fixture is session-scoped against one real, shared Postgres database with
  no per-worker isolation, so parallelizing would risk real cross-worker
  collisions for a suite that's already fast (753 tests, ~53s). Not applied.
  Checked `prestart.sh`'s lack of a wait-for-DB retry loop -- already
  covered by `compose.yml`'s/CI's own `condition: service_healthy` /
  `--wait` gating at the orchestration level. Not applied.
- No branch protection exists on this repo (private, free tier -- confirmed
  via the GitHub API), so the new path-filter-gated jobs needed no
  `alls-green`-style wrapper the way `playwright.yml` already has.
- Found, deliberately not fixed (Phase 14's job, matching the Phase 9.5/9.6
  precedent for `.github/workflows/*` template logic): `deploy.yml`'s
  leftover `if: github.repository != 'fastapi/full-stack-fastapi-template'`
  template-org check.
- Full gate: `ruff check`/`ruff format --check`/`mypy app`/`ty check app`
  clean, backend suite 753 tests green, `prek run --all-files` clean
  (including `zizmor` on every workflow edit). No application code touched
  this phase, so the suite result is identical to before -- confirming
  nothing broke, not that anything was fixed.
- **Review fixes (PR #18):** `code-reviewer` and `python-reviewer` each
  caught one real issue before merge. `python-reviewer`: the non-root
  user's blanket `RUN chown -R app:app /app` forced overlay2 to copy up
  every file from every earlier layer, quietly giving back ~430MB of the
  size win (1.14GB with it, re-measured 707MB without) -- fixed by
  chowning each layer's own output at creation time (`COPY --chown`,
  `chown` appended inside the same `RUN` as `uv sync`) instead of one
  blanket pass at the end. `code-reviewer`: the GHA Docker-cache
  mechanism as first written would have been silently inert in CI, since
  the `gha` cache backend needs buildx's `docker-container` driver and no
  workflow provisioned it (confirmed locally: `docker buildx ls` shows
  the default driver is plain `docker`) -- fixed by adding
  `docker/setup-buildx-action` (`driver: docker-container`) before the
  relevant `docker compose build` steps. Also fixed: a path-filter gap
  (missing `pyproject.toml`/`uv.lock`/`bun.lock` in all three
  `changes`-job filters, which could have let a dependency-only bump
  skip its own test job) and an unquoted `${WEB_CONCURRENCY:-4}`
  expansion in the Dockerfile's `CMD`. Re-verified after fixes: rebuilt
  image, confirmed non-root `app` user can still import `app.main` and
  the backend container reports healthy, full gate re-run clean.

## Phase 2 — done pending PR review

- `botpit.games.base`: `Game[State, Move]` contract, frozen pydantic state,
  `IllegalMoveError`, `PlayerView`, `hand_view` hook; `cards.py` shared types
  and deterministic `rng_for(state)`; `GAME_CATALOG` registry.
- Four games, each with `rules.md` and scripted tests: Classic UNO (92 tests),
  UNO Flip! (96), UNO Show 'em No Mercy (104), Hearts (46), all passing the
  shared contract suite across their seat ranges; catalog round-trip tests.
- Hearts proved the contract generalises: no change to base/registry needed
  beyond one registry line.
- Total suite: 458 tests, ~18 s.

## Phase 6 — done 2026-09-12 (branch phase-6-auth)

- `authz`: roles/permissions tables + seed migration (4 default roles, 12
  permissions), `load_permissions`, `require_permission` FastAPI dependency,
  roles admin routes, default `player` role on signup, `/users/me/permissions`.
- `rooms-infra`: Redis client singleton, Lua sliding-window rate limiter +
  dependency, join codes (Crockford base32), ZSET join queue with
  offers/sweep/ETA, CI starts Redis.
- `rooms-tables`: `Room`/`RoomSeat`/`RuntimeSetting`/`FeatureFlag`/
  `FeatureFlagOverride` tables and migration (closes the deferred
  `room_role -> room` FK from the authz migration), admin-adjustable
  settings and feature flags with a 5s per-process cache and precedence
  (user override > role override > global), `/admin/settings`, `/admin/flags`,
  `/flags/me`.
- Suite: 651 tests, ruff/mypy/ty/prek all clean.
- Ahead of the original phase split: the Redis capacity primitives and the
  runtime-settings/feature-flags tables that §1.8/§1.10 assign to Phase 7
  are already built. Phase 7 now only needs the `RoomService`, HTTP routes,
  `SubscriptionGuard` and wiring `GameRunner` into the app lifespan.
- Housekeeping: Dependabot opened PRs #4-#9 from the template's
  `dependabot.yml`; left for the owner.

## Phase 3 — done 2026-09-12 (PR #10, with Phases 4-5)

- `backend/app/referee/`: `SeatSpec`, `SeatController`, `HumanController`,
  `PolicyController`, `ScriptedController`, `fallback_move`, `Referee`
  (turn loop with retries, deadlines, fallback, event publication,
  `replay`), `GameRunner`. 13 tests: every game finishes with policy seats
  and replays from the log; a human seat fed through the runner produces
  the same final state as a scripted one; agent retry-then-fallback; human
  retry-until-deadline; player subscribers only see their own views; stop
  publishes `room.stopped`.
- Event schema gained `game.move_rejected`, `game.turn_timed_out`,
  `game.view_updated`; `game.started`/`turn_started`/`move_applied`/
  `finished` carry more fields.
- Table-talk hooks: the referee calls an optional `say` after each move
  and fans the line out to other controllers' `hear`.
- Phase 4 (same branch): `app.games.move_schema` (self-contained JSON
  schema per game, `move_adapter` on the contract), `app.agents.persona`
  (6 built-ins), `app.agents.prompt` (two-call prompts, 3500-token budget,
  PlayerView-only by construction), `app.api.views.view_payload`,
  `tests/security/` fuzz suites for prompt and event-stream privacy.
- Phase 5 (same branch): `app.llm.client` (protocol, errors, retries,
  think-tag stripping, metrics hook), `OllamaNativeAdapter`,
  `OpenAICompatAdapter`, `ModelRegistry` (+ `LLM_MODEL_OVERRIDES`),
  `FakeLLMClient`, `AgentController` (repair loop, reasoning event, chat
  call), live tests for a raw completion and a full agent turn per model
  (`-m ollama`). Live agent turn per model on the 6 GB card, cold:
  gemma3:4b 10 s, llama3.1:8b 14 s, qwen3:4b 8 s (thinking off),
  phi4-mini 8 s; all four chose a legal move, gemma needed the
  discriminator-required schema fix, qwen needed the echo-field-tolerant
  legality check.

## Phase 2b — done 2026-09-12 (PR #3): template adopted

Owner decision 2026-09-12: https://github.com/fastapi/full-stack-fastapi-template
is the base. Done:
- Template tree vendored whole (backend/, frontend/, packages/, scripts/,
  hooks/, .github/, .claude/, .agents/, .vscode/, img/, compose files, root
  docs). Python 3.14 via uv, Bun 1.4.
- botpit modules ported to `backend/app/`: `games`, `events`, `telemetry`
  (renamed from `logging`, which shadowed the stdlib when scripts run from
  `app/`), `llm`, `middleware.py`, `api/routes/health.py`.
- Settings folded into `app/core/config.py` (upper-case fields);
  `FASTAPI_ENV` now accepts staging/production; `env_name` property.
- `app/main.py`: lifespan (logging, event bus, LLM health gate), wide-event
  middleware, frontend mount guarded by directory existence.
- Dropped: our async SQLAlchemy layer, Alembic dual-dialect setup, Caddy,
  Makefile, our CI workflow, `.env.<env>` files (template uses one `.env`).
- compose: `redis` service, botpit env vars on `backend`, dev override
  publishes db/redis on 5433/6380 and disables the LLM startup gate inside
  compose (Windows Ollama is not reachable from a bridge network).
- Verified: ruff, mypy strict, ty clean; 509 tests green (template suite +
  ours + Redis integration) against compose Postgres/Redis; native
  `fastapi run` healthy with Ollama 4/4; frontend builds and is served at
  `/`; backend image builds; full compose stack up behind Traefik.
- Open: template workflows for releases/deploys are kept verbatim and will
  be adapted in Phase 14; `docs/deployment.md` now defers to the template's
  `deployment.md` for mechanics.

## Phase 1 — done (PR #1 merged 2026-09-12)

Delivered on `phase-1-scaffold`:
- Research: `docs/local-model-setup.md`, `docs/deployment.md`.
- `pyproject.toml` (uv, ruff, mypy strict, pytest-asyncio), `src/botpit/config.py`
  (pydantic-settings, `BOTPIT_ENV` picks `.env.<env>`), `.env.dev`,
  `.env.staging.example`, `.env.production.example`.
- `botpit.logging`: wide events (`wide_event` context manager, contextvar,
  correlation inheritance), JSON + console formatters, tail sampler.
- `botpit.events`: envelope with `visibility` (public / seat:N / privileged),
  typed payloads, `filter_for_subscriber` (privacy boundary), `EventBus`
  protocol with `InMemoryEventBus` and `RedisStreamsEventBus` (WATCH/MULTI
  seq assignment, XREAD fan-out, catch-up from last id), shared contract
  tests on fakeredis and real Redis.
- `botpit.db`: SQLAlchemy 2.0 async base/session, portable mixins, Alembic
  env for SQLite + Postgres, `runtime_settings` table + migration 0001,
  programmatic `upgrade_to_head`, autogenerate-diff guard test.
- `botpit.llm.health`: provider-agnostic probe (`ollama_native`,
  `openai_compat`), startup gate.
- `botpit.app`: factory, lifespan (logging, migrate, bus, LLM gate),
  `/health`, `/health/llm`, request wide-event middleware.
- Infra: Dockerfile (multi-stage uv, non-root), compose (app, postgres:17,
  redis:8, `llm` and `proxy` profiles), dev override (host network so the
  container reaches Windows Ollama; host ports 5433/6380), Caddyfile,
  Makefile, CI workflow (lint, unit, integration with services, docker build).
- Verified locally: unit suite green; integration suite green against
  compose Postgres + Redis; app healthy natively and in compose against
  the real Windows Ollama (4/4 models present).

Open decisions raised mid-build:
- GitHub branch protection on `master` is unavailable (private repo on the
  free plan). Protection is by discipline: PR-only merges, never push to
  master directly.
- Original `DECISIONS.md` missing; reconstructed one committed. Owner to
  supply the original for a merge.
- Ollama on Windows binds 127.0.0.1, so the dev app container uses host
  networking instead of `host.docker.internal`. Servers do not have this
  problem (the LLM is a compose service there).
