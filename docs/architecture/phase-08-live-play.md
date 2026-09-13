# Phase 8 — First live mixed game (terminal + API)

Status: done, branch `phase-8-live-game`, 2026-09-13. Two independent
reviews (correctness/concurrency, security) ran against this phase; their
findings and fixes are included below since they materially changed the
shipped code.

Historical snapshot of the live-play HTTP surface as it stood when this
phase (including its review fixes) completed. Not updated for later
changes — see `docs/architecture/latest_architecture.md` for the current
state, and `docs/rooms.md` for the living documentation.

## What was built

The minimal live-play surface a human plays through: `/view`, `/moves`,
`/moves/schema`, `/events`. This is HTTP polling, not a websocket/SSE
transport — that stays Phase 10's job — but it is a working, tested
surface either way, and the first real caller of Phase 7's
`subscription_guard`.

- **`RoomService` gains three methods**: `own_seat(user, room_id)` resolves
  the caller's own human seat server-side from `RoomSeat.user_id`
  (`NotSeatedError` if none — never trusts a client-supplied seat number).
  `live_view(user, room_id)` reads the caller's live `PlayerView` straight
  from `GameRunner`'s in-memory `RunningGame`, never the database (which
  holds no per-turn game state) — `RoomNotRunningError` if `GameRunner` has
  no entry. `submit_move(user, room_id, move)` adds a `NotYourTurnError`
  check against `game.current_seat(state)` before forwarding to
  `GameRunner.submit_move`, so an out-of-turn submission gets an immediate
  409 instead of silently queuing and being dropped by the next turn.
- **HTTP routes** (`app/api/routes/rooms.py`): `GET /{room_id}/view`,
  `POST /{room_id}/moves`, `GET /{room_id}/moves/schema`,
  `GET /{room_id}/events`. A shared `_subscriber_for` helper wraps
  `subscription_guard` + `current_permissions` for `moves/schema` and
  `events` (denial → 404, matching every other room route's
  never-leak-existence rule); `view`/`moves` use `own_seat` instead
  (denial → 403, since these are about playing your own seat, not
  administrative visibility). `events` polls `EventBus.history(room_id,
  since, limit)` and drops anything `filter_for_subscriber` denies,
  returning `{"events": [...], "next_since": ...}` for the next poll.
- **Deferred, precisely**: a real push transport and an admin/spectator
  multi-seat "focus view" are both Phase 10's job.

## Key implementation decisions made during this phase (initial build)

- **`own_seat` never trusts a client-supplied seat number** — it is always
  derived server-side from `RoomSeat.user_id`. This closes an entire class
  of "play as another seat" bugs before they could exist, rather than
  relying on a per-route check to catch it.
- **`NotYourTurnError` gives an immediate 409** rather than accepting an
  out-of-turn move and silently dropping it — a deliberate choice to fail
  loud at the API boundary instead of failing silent inside
  `HumanController`'s queue.

## Review fixes (two independent reviews: correctness/concurrency, security)

- **`GameRunner` never evicted a finished game (HIGH, correctness)**: a
  naturally-finished or stopped room's `RunningGame` stayed in `_games`
  forever, so `GET /view`/`POST /moves` kept finding a dead game instead of
  ever raising `RoomNotRunningError` — a move submitted after the game
  ended could look "accepted" (200) with nobody left to read it, and it was
  also a per-room memory leak. Fixed at the one place that knows a game
  truly ended: `GameRunner._watch` now pops `self._games[room_id]`
  alongside the existing watcher cleanup.
- **Cross-thread `asyncio.Queue.put_nowait` (HIGH, both reviews)**: `POST
  /moves` is a sync route (Starlette threadpool thread) calling
  `HumanController.submit`, which the referee concurrently awaits on the
  event-loop thread — `asyncio.Queue` is documented safe only for
  same-thread use or `call_soon_threadsafe`. Fixed: `HumanController`
  captures its loop at construction (always on the loop thread) and
  `submit` now marshals through `loop.call_soon_threadsafe`.
- **Unbounded move queue (CRITICAL, security)**: nothing capped how many or
  how large the dicts a client could pile into `HumanController`'s queue,
  and `POST /moves` had no rate limit at all. Fixed three ways:
  `HumanController`'s queue is now `maxsize=1` with replace-not-grow
  semantics (only the latest unconsumed submission is ever meaningful);
  `MoveSubmission.move` is size-capped at 4096 bytes (a `field_validator`
  → 422 over the limit); a new `RATE_LIMIT_ROOM_MOVE` setting (30/10s)
  gates the route.
- **Unvalidated `since` on `/events` (MEDIUM, security)**: a malformed
  cursor reached `int(since.split("-", 1)[0])` unguarded and 500'd instead
  of 400ing. Fixed with a query-parameter pattern (`^\d+-\d+$`) → 422 on
  anything else.
- **Reviewed and accepted as-is (no change needed)**: seat/turn identity
  is always server-derived, no mass-assignment vector in `MoveSubmission`;
  `filter_for_subscriber` applies unconditionally to every event with no
  bypass path; `next_since` advancing past a hidden event is a deliberate,
  documented tradeoff (the alternative — never advancing — would let a
  caller get stuck polling forever behind an event it can't see).
- **Full suite after fixes: 737 tests, gate clean** (two `prek` passes —
  the frontend SDK regenerates once for the four new routes, then holds).

## Module layout introduced

No new top-level module — this phase extends `app/rooms/service.py` and
`app/api/routes/rooms.py` (both from Phase 7) with the methods and routes
listed above.

## Tests and what was verified

11 new tests initially (`tests/rooms/test_rooms_live.py`) against a real
2-human-seat `uno_classic` game (real `GameRunner`, no mocks): auth-gate
smoke tests per route (`NotSeatedError`/`RoomNotRunningError`/
`NotYourTurnError` → 403/409/409), a full submit-and-verify-via-`/events`
happy path, and two security tests proving `game.view_updated` (a seat's
full hand) never reaches another seat through `/events` for a
non-privileged player, while the privileged creator sees both. Full suite
at that point: 731 tests. After the review-fix pass above: **737 tests**,
ruff/ruff-format/mypy strict/ty/prek all clean.
