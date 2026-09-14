# Leaderboard (Phase 12)

Status: done. DECISIONS.md §11: "leaderboard per human account and per
model, with aggregate admin views."

## Why `room_seat` alone is enough

A finished game's `GameRecord.winner_seat` (docs/games.md) names a seat,
not a player -- `RoomSeat` is what ties that seat back to the account or
model that actually played it. No separate per-game participant snapshot
table is needed to do that durably: `RoomService.start()` refuses a room
that isn't `"waiting"`, and nothing anywhere puts a room back into
`"waiting"` after that (`join`/`force_start` are likewise gated on
`"waiting"`) -- so a room plays exactly **one** game for its entire
lifetime. `room_seat` already *is* that one game's durable participant
record, immutable from the moment `start()` runs.

`app.leaderboard` joins `game_record` to `room_seat` on `room_id` and
credits a win when `game_record.winner_seat == room_seat.seat`.

## What counts

Only `game_record.outcome == "finished"` rows: a `"stopped"` game's
`winner_seat` is `None` (it never produced a result), so it contributes
neither a game-played nor a win/loss to either leaderboard. It still
happened -- it's just not what a leaderboard is counting.

## Two views, one query shape

- `leaderboard_by_account`: one row per human account (`room_seat.kind ==
  "human"`), grouped by `user_id`. `display_name` is
  `coalesce(full_name, email)`, computed in SQL rather than in Python so a
  five-column `select()` doesn't need a fifth positional argument (SQLModel's
  typed `select()` overloads only go up to four).
- `leaderboard_by_model`: one row per model name (`room_seat.kind ==
  "agent"`), grouped by `model`. Deliberately not grouped by persona too --
  DECISIONS.md §11 says "per model," and a model's leaderboard entry is
  meant to answer "how does this model play," independent of which persona
  prompt happened to be layered on top of it in any given game.

Both take an optional `game` filter (a game slug, e.g. `"hearts"`) scoping
the aggregate to one game type -- without it, a strong Hearts record and a
strong Classic UNO record combine into one number, which is rarely what
"how good is this account/model" actually means once more than one game
type has real history.

## Routes (`app/api/routes/leaderboard.py`, prefix `/leaderboard`)

| Route | Notes |
|---|---|
| `GET /leaderboard/accounts?game=` | `list[LeaderboardEntry]`, ordered by wins desc then games played desc. |
| `GET /leaderboard/models?game=` | Same shape, grouped by model instead of account. |

Both gated by `leaderboard:view` -- granted to every default role
(`admin`/`player`/`spectator`, `app/authz/permissions.py`), not admin-only:
a leaderboard is meant to be seen by the players in it, not filed away as
an internal report. "Aggregate admin views" (DECISIONS.md §11) just means
this same data is also visible from the admin side, not that it needs a
separate, more-restricted endpoint.

## Frontend

`/leaderboard` (sidebar nav item, shown to everyone): two tabs (Accounts,
Models) over the same `LeaderboardEntry` shape, sharing one column set
(`components/Leaderboard/columns.tsx`), plus a game-filter dropdown reusing
the same `GAMES` catalog `CreateRoomDialog` already hardcodes
(`lib/games.ts`, extracted from `CreateRoomDialog.tsx` during this phase so
the two didn't duplicate the same four-entry list).

## Left for later (explicitly, not silently)

- **No persona-level breakdown.** Deliberately out of scope per DECISIONS
  §11's own wording ("per model," not "per model per persona") -- would be
  a natural, cheap follow-on if wanted (persona is already on `room_seat`).
- **No pagination.** Every account/model that has ever finished a game
  comes back in one response. Fine at this project's scale; would need
  `limit`/`cursor` query params (matching `GET /games/{id}/events`'s own
  pattern) if the account/model count ever grows large enough to matter.
- **A pre-existing games-archived-before-a-fix problem is generic to this
  whole approach, not specific to leaderboard**: if a later change alters
  what `room_seat.model`/`kind` mean for future games, older archived
  `GameRecord`s still join against whatever `room_seat` rows existed when
  *they* were played -- there is no re-derivation step, by design (the
  point of `room_seat` being immutable-after-start is that old history
  never silently reinterprets itself).
