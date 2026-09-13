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
| 9.6 | Upgrade to latest stable versions | done 2026-09-13 (branch phase-9.6-dependency-upgrade, PR pending) |
| 10 | Frontend: play, spectate, admin, focus view | |
| 11 | Chat and personas | |
| 12 | Leaderboard | |
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
