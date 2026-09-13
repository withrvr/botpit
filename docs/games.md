# Game history (Phase 9)

Status: done. DECISIONS.md §12: "the event log is the source of truth;
finished games replay from it." The live `EventBus` (Redis Streams or
`InMemoryEventBus`) is a transport, not durable storage -- this phase adds
what actually survives a Redis flush or an in-memory bus restart: two
Postgres tables and the HTTP routes that read them.

## Schema

- `game_record`: `id` (the referee's own `game_id`, a hex string primary
  key -- not a fresh id, so `correlation.game_id` on every event ties
  straight back to this row), `room_id` (FK `room.id`, `CASCADE`), `game`
  (slug), `seat_count`, `seed`, `outcome` ("finished" | "stopped"),
  `winner_seat`, `ranking` (JSON `list[int]`), `scores` (JSON `list[int]`),
  `move_count`, `started_at`, `finished_at`.
- `game_event`: `id` (uuid pk), `game_id` (FK `game_record.id`, `CASCADE`),
  `room_id` (denormalized from `game_record.room_id` -- lets an admin query
  "every archived event for this room" without a join, the same tradeoff
  `room_role` already makes), `seq` (the bus's own monotonic order,
  preserved verbatim), `type`, `ts`, `visibility` (JSON), `payload` (JSON),
  `correlation` (JSON), `stream_id`. Unique on `(game_id, seq)`.

Both cascade from `room` (and `game_event` further cascades from
`game_record`): deleting a room's row -- not something any code path does
yet, but the constraint is there for when one does -- cleans up its whole
history automatically, matching how `room_seat`/`room_role` already behave.

## Archiving: `RoomService._archive_game`

Hooked into `_finish_room`, `GameRunner`'s completion callback (the same one
Phase 7 added to keep a room's persisted status from diverging from
whether a game is actually running -- see `docs/rooms.md`). When a game
ends, `_archive_game`:

1. Reads the room's *entire* event history from the live bus
   (`EventBus.history(room_id, since=None, limit=100_000)`).
2. Keeps only events carrying a `correlation.game_id` -- everything the
   `Referee` published (`game.*`/`agent.*`/`chat.*`, and its own
   `room.stopped` on cancellation). `RoomService`'s own lifecycle events
   (`room.created`, `room.seat_filled`) carry no `game_id` and are excluded
   -- they already have their own row in `room`/`room_seat`.
3. Builds one `GameRecord` from the `game.started` event (game/seat_count/
   seed) and the `game.finished` event if one exists (winner_seat/ranking/
   scores -- absent for a "stopped" outcome, since the game never actually
   finished).
4. Writes it, `session.flush()`es to force the row to exist before the
   FK-referencing event rows (explicit, not left to the flush planner's
   dependency sort), then writes one `GameEvent` per archived event, and
   commits.

**The status write and the archive write are two separate steps, and that
separation is load-bearing.** `_finish_room` only *writes* `room.status`
when it is still `"in_progress"` -- an explicit `stop()` call races this
exact callback (both `stop()`'s own `await runner.stop(...)` and this
watcher await the same cancelled task; either can resume first) and
whichever already wrote `"stopped"` must not be double-written. But this
callback still fires exactly once per game regardless of which side won
that race, so **archiving is never nested inside that guard** -- it always
runs. An earlier draft of this phase nested it, which meant archiving was
silently skipped every time `stop()` happened to win; the regression test
(`test_finish_room_archives_even_when_stop_already_won_the_status_race` in
`tests/rooms/test_game_history.py`) reproduces that race deterministically
(construct the room already `"stopped"`, i.e. simulate `stop()` having
already won, before calling `_finish_room`) rather than relying on real
asyncio scheduling luck.

Archiving is also best-effort and isolated: a failure here is caught and
logged (`log.exception`), never allowed to keep the room's own status stuck
`"in_progress"` -- that would reopen the exact capacity leak Phase 7 fixed,
for a much worse reason.

### Left for later (explicitly, not silently)

- **No retry for a failed archive.** If `_archive_game` raises (a DB
  hiccup), the room's status still updates correctly, but that game's
  history is gone for good -- no backlog/reconciliation job re-attempts it.
  Acceptable for local development; a production deployment would want one.
- **No pagination past `limit=100_000`** when reading the live bus or the
  archived rows. Generous for how long any of these games actually run;
  not a real cap that could truncate a legitimately long game's history.
- **Archiving is only as complete as the live bus's own retention.** Both
  `InMemoryEventBus` and `RedisStreamsEventBus` are bounded per room
  (`EVENT_STREAM_MAXLEN`, default 10,000 events -- *per room*, not shared
  across the whole bus). `_archive_game` reads whatever `bus.history()`
  still has at the moment the game ends; if a single game's own event count
  ever exceeded that cap before archiving ran, its earliest events
  (possibly `game.started` itself) would already be evicted, and the
  archive would be silently incomplete rather than erroring. Ten thousand
  events for one game is enormous headroom for any of these games' actual
  lengths (a few hundred moves at most, a handful of events each) -- not a
  practical concern today, but a real dependency worth naming rather than
  assuming away. Archiving incrementally during play, rather than only at
  game end, would remove the dependency entirely; not built here.

## HTTP routes (`app/api/routes/games.py`, prefix `/games`, tag `games`)

Every route resolves admission through `app.rooms.guard.resolve_subscriber`
-- the exact same function Phase 8's live `/rooms/{room_id}/events` uses,
factored out of `app/api/routes/rooms.py` so both share one place. A
finished game's privacy rules never diverge from the room's own: a private
room stays private after its game ends, an unlisted room stays
link-only, and another seat's hand stays hidden from a non-privileged
viewer even in the archive.

| Route | Notes |
|---|---|
| `GET /games/{game_id}` | The `GameRecord` summary (`GameRecordPublic`). 404 for an unknown id or a denied subscriber -- never distinguished, same rule as every other room resource. |
| `GET /games/{game_id}/events` | The durable counterpart to the live `/rooms/{room_id}/events`. `since`/`next_since` here are the archived `seq` integer, not a live bus cursor -- there is nothing left to poll for once a game is over, only pages to walk through. Each row is reconstructed into an `EventEnvelope` and passed through the identical `app.events.filter_for_subscriber` the live route uses -- one privacy-filtering code path for both live and archived events, not two that could drift apart. |
| `GET /games/{game_id}/replay?seat=&upto=` | Rebuilds one seat's `PlayerView` at any point in the game's history, straight from the archived move log, via `Referee.replay` (already existed, proven by its own unit test to reproduce a live game's exact final state) with the move list truncated to `upto` moves (default: all of them). A non-privileged caller may only ever request their own seat (403 otherwise); a privileged caller (creator or `room:view_seat_detail`) may request any seat, one at a time -- same shape as the live `/view`, since a cross-seat admin "focus view" is explicitly Phase 10's job, not this route's. Omitting `seat` defaults to the caller's own (400 if they hold none); `upto` past the game's actual move count is 422. |

## Design note: why `/games`, not `/rooms/{room_id}/games/...`

A `game_id` is already globally unique and self-sufficient -- a client that
only knows a `game_id` (from a future leaderboard listing, say) shouldn't
need to also know its `room_id` to look it up. Every route resolves
`room_id` from the `GameRecord` internally before running the identical
admission check `/rooms/{room_id}/events` uses, so this costs nothing in
either security or consistency.
