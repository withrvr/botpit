# Capacity and queueing

Status: designed and built in Phase 7 (Redis pieces first, rooms wire on top).
Research and rejected alternatives: `docs/research/stack-choices.md` §3, §5.

## Caps

| Cap | Setting | Scope | Overflow |
|---|---|---|---|
| Concurrent games | `MAX_CONCURRENT_GAMES` (default 4, runtime-overridable later) | `queue:global` | FIFO queue |
| Seats per room | game's seat count | `queue:room:{room_id}` | FIFO queue |
| Viewers per room | `MAX_VIEWERS_PER_ROOM` (20) | per room | refuse (viewers are not queued) |
| Total connections | `MAX_CONNECTIONS` (200) | process | refuse websocket upgrade |
| Per-account actions | `RATE_LIMIT_ROOM_CREATE` (5/60), `RATE_LIMIT_ROOM_JOIN` (20/60), `RATE_LIMIT_CHAT` (10/10) | `rl:{scope}:{user_id}` | 429 / ws error |

Rate limits are strings `"count/seconds"` in the environment, parsed to
`(count, seconds)` by `Settings`. Defaults are dev-generous; production tightens
them in `.env`.

## Rate limiter (`app/ratelimit.py`)

Sliding-window log per key, one Lua script per hit so the check-and-record is
atomic across workers:

```
ZREMRANGEBYSCORE key -inf now-window   -- forget hits older than the window
ZCARD key < limit ?                     -- room left?
  ZADD key now nonce; PEXPIRE key window; return {1, 0}
else return {0, oldest_score + window - now}   -- ms until the oldest hit ages out
```

`check_rate(redis, key, limit, window_s, now_ms=None) -> (allowed, retry_after_s)`
is the whole API. `now_ms` is injectable for tests; the nonce member keeps two
hits in the same millisecond distinct. The key expires with the window, so idle
users cost nothing.

- HTTP: `Depends(rate_limit(scope, limit, window_s))` (or
  `rate_limit_from_setting("room_create", settings.RATE_LIMIT_ROOM_CREATE)`)
  reads the user from `CurrentUser`, keys `rl:{scope}:{user_id}`, and raises
  `RateLimited` which `app.main` renders as
  `429 {"detail": "rate limited", "retry_after": n}` with a `Retry-After` header.
- Websocket chat: the handler calls `check_rate_ws` directly and replies
  `{"type": "error", "code": "rate_limited", "retry_after": n}`; the message is
  dropped, the socket stays open.
- Every denial logs `botpit.ratelimit` at INFO with the key.

Why Lua and not a library: `slowapi` cannot rate-limit websocket handlers and
insists on `request: Request` in every signature; `fastapi-limiter` would pull
in `pyrate-limiter` for ~15 lines of Python and its default identifier (IP +
path) has to be replaced anyway. Why not `INCR` + `EXPIRE`: a fixed window lets
a user spend 2x the limit across a window boundary; the ZSET log does not.

## Join queue (`app/rooms/queue.py`)

One `JoinQueue` per Redis client, parameterised by `scope`:
`"global"` for the concurrent-game cap and `f"room:{room_id}"` for seats.

| Key | Type | Meaning |
|---|---|---|
| `queue:{scope}` | ZSET user -> enqueue ms | the line; `ZADD NX` makes join idempotent, `ZRANK` is the position, `ZREM` leaves from the middle |
| `offers:{scope}` | ZSET user -> deadline ms | live offers, scanned by `sweep` |
| `offer:{scope}:{user}` | string seat, `SET NX PX` | the held seat; Redis expires it independently of the sweeper |
| `scores:{scope}` | HASH user -> original score | restored on requeue so a missed offer costs no place |
| `misses:{scope}:{user}` | counter, 1 h TTL | expired offers; dropped at `max_misses` (2) |

Flow:

1. `enqueue(scope, user)` -> 1-based position. Re-joining is a no-op.
2. A seat frees -> `offer_next(scope, seat)` runs one Lua script:
   `ZPOPMIN queue`, `SET offer NX PX ttl`, `ZADD offers deadline`,
   `HSET scores`. Returns `(user, deadline_ms)` or `None` when nobody waits.
   The room layer publishes `seat_offer` on the room stream so the user's
   websocket worker pushes it.
3. `accept(scope, user)` -> `GETDEL offer` gives the seat (or `None` if it
   expired) and clears the offer/score/miss bookkeeping.
4. `sweep(scope)` every ~5 s from one worker (Redis `SET lock NX` leader):
   `ZRANGEBYSCORE offers -inf now`; for each expired offer `ZREM` it (losing
   the race to `accept` means skip), delete the seat hold, bump `misses`, then
   either requeue at the **original** score or drop after `max_misses`.
   Returns the requeued users so the caller can offer the freed seat again.
5. `snapshot(scope, user, oldest_started_at)` -> `QueueStatus(position,
   length, eta_seconds)` pushed to waiters on every `queue_update`.

Every enqueue/leave/offer/accept/requeue/drop logs `botpit.queue` at INFO with
the key. Keyspace notifications are deliberately not used (off by default,
at-most-once); the `offers` ZSET plus a sweep is deterministic.

### Fairness

- Pure FIFO by enqueue timestamp. `ZADD NX` means one place per user per scope.
- A missed offer never costs a place: the user returns at their original score,
  still ahead of everyone who joined after them. Two misses in a row and they
  are dropped (they can rejoin at the back).
- Offers are atomic (`ZPOPMIN` + `SET NX` in one script) so two workers freeing
  seats at once cannot offer the same user twice or skip someone.
- The queue is ephemeral: it lives in Redis only and does not survive a
  restart (DECISIONS §12: Redis is never the source of truth for game state,
  and a queue is not game state).

### ETA heuristic

`eta_seconds(position, oldest_started_at, typical_game_s=900)`:
the oldest running game is assumed to finish after `typical_game_s`, later
places wait one typical game each: `max(0, typical - elapsed) + (position - 1) * typical`.
`None` when nothing is running (no basis for a guess). Pessimistic single-lane
estimate on purpose; refine with a measured median game length once games
persist durations.

## What rooms wire on top

- `POST /rooms` with `rate_limit_from_setting("room_create", ...)`,
  `POST /rooms/{code}/join` with `room_join`; chat via `check_rate_ws`.
- Global cap: a room may start only if running games < `MAX_CONCURRENT_GAMES`,
  else the room enqueues on `"global"` and starts on its offer -- `start`
  itself enqueues (or, at position 1, pops itself off) rather than
  `create`'s own hard refusal, and `RoomService._finish_room` tries to
  promote the next globally-queued room once a slot frees.
- Seat cap: a full room enqueues joiners on `f"room:{id}"`; a leaving human
  triggers `offer_next(scope, seat)`.
- **No standalone sweeper task (Phase 7's original plan for this).** Phase 16
  wired the queue fully but reclaims expired offers opportunistically
  instead of via a leader-elected background task: `join_by_code` sweeps as
  a side effect of a caller's own retry, and `GET /{room_id}/queue` sweeps
  on every poll while a waiting caller's own queue UI is open. Between
  those two, the realistic paths are covered (either the offeree is still
  watching, or someone else eventually tries the room again) -- but a
  room whose only waiter closes their tab, where nobody else ever touches
  that room's queue, leaves that one offer stale until something does. A
  real background sweeper would close this fully; not built, since neither
  path it would add coverage for is the common case.
- **No `seat_offer` / `queue_update` push events either.** The frontend
  polls `GET /{room_id}/queue?scope=seat|global` (`QueueStatus`, including
  `offered`/`offer_expires_in_seconds` for a live seat offer) rather than
  receiving a push notification over the room's event stream -- simpler,
  and the room already flips to `"in_progress"` over the existing
  websocket once a promoted/accepted seat actually starts a game, so the
  wait itself is the only thing polling covers.
