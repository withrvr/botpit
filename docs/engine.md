# Engine

## Game module contract (Phase 2)

Every game is a package under `src/backend/app/games/<slug>/` exposing a `GAME`
instance of `app.games.base.Game[State, Move]` and a `rules.md`. It is
registered by one line in `backend/app/games/__init__.py` (`_GAMES`). Nothing
else changes when a game is added: the referee, websocket layer and
frontend only see the contract.

### Types (`backend/app/games/base.py`)

| Type | Role |
|---|---|
| `GameState` | frozen pydantic base: `slug`, `seed`, `seat_count`, `move_count`, `hands`, `scores`, `eliminated`. Games extend it; collections are tuples. |
| `Move` | frozen pydantic base with `kind` (discriminator) and `seat`. Games define a discriminated union. |
| `PlayerView` | the only thing a seat sees: own `hand`, `hand_counts`, `table` (public game-specific extras), own `legal_moves`, `phase`, `current_seat`, `direction`, `scores`, `eliminated`, `terminal`. |
| `IllegalMoveError` | raised by `apply` with `.move` and `.reason`; the referee turns `.reason` into a retry prompt for agents. |

### Methods

| Method | Contract |
|---|---|
| `new_game(seed, seat_count)` | deterministic from the seed; raises `ValueError` outside `min_seats..max_seats` |
| `parse_move(dict)` | validates untrusted input (websocket or model output) into the game's move union |
| `current_seat(state)` | exactly one seat, or `None` iff terminal; no simultaneous phases |
| `legal_moves(state, seat)` | exactly what `apply` accepts for that seat; empty for other seats; non-empty for the current seat unless terminal |
| `apply(state, move)` | pure; new state with `move_count + 1`; `IllegalMoveError` otherwise, input untouched |
| `ranking(state)` | best first, a permutation of all seats, defined at every state |
| `player_view(state, seat)` | the privacy gate (DECISIONS.md §9) |
| `rules_markdown()` | the game's `rules.md`, handed to agents verbatim |
| `describe_move(move)` | one line for logs and chat context |
| `move_adapter` (ClassVar) | the game's `MOVE_ADAPTER` (`TypeAdapter` over its discriminated move union); `parse_move` validates through it and `app.games.move_schema.move_json_schema` derives the LLM output schema from it |

### Baked decisions

- Randomness: `cards.rng_for(state)` seeds a `random.Random` from
  `(seed, move_count)`. Replaying the move list reproduces every shuffle;
  no generator state is stored.
- Wild colour choice is part of the play move, never a second move.
- Draw-then-play is two moves (`draw`, then play the drawn card or `pass`),
  because the intermediate state is a real decision point for an agent.
- UNO call is a flag on the play move; catching a missed call is a move
  available to the current seat that does not consume their turn.
- Single round per game in v1; `scores` carries round points so
  multi-round play can wrap it later.
- Eliminated seats (No Mercy) rank below survivors, last eliminated highest.
  Ties break by seat index.

### Contract tests

`tests/unit/games/contract.py` holds `ALL_CHECKS`; every game's test module
runs them across its seat range: metadata, determinism, serialisation
round-trips (state, move, view), rejection of wrong-seat and stale moves,
random playouts terminate for many seeds, ranking is a permutation, and
`player_view` privacy (own hand only, counts correct, only own legal moves,
and reordering other seats' hands leaves the view byte-identical).

### Games in the catalog

| Slug | Title | Seats | Notes |
|---|---|---|---|
| `uno_classic` | Classic UNO | 2-10 | Wild Draw Four challenge, UNO catch window, draw-then-play |
| `uno_flip` | UNO Flip! | 2-10 | double-faced cards, light/dark sides, `hand_view` shows active faces only |
| `uno_no_mercy` | UNO Show 'em No Mercy | 2-10 | stacking, 7/0 rules, mercy elimination at 25 cards |
| `hearts` | Hearts | 3-6 | sequential hidden passing, follow suit, shoot the moon |

Each package's `rules.md` is the agent-facing rule text and records every
rule decision the official rules leave open (draw-only-when-stuck in Flip,
no-progress termination, tie-breaks). `Game.hand_view(state, seat)` is the
one hook a game overrides when a holder must not see part of their own card.

## Referee (Phase 3)

`app.referee` drives one game to completion. It owns turn order,
validation, application and win detection; the game module owns the rules.

- `SeatController` is the only interface between the referee and a player:
  `async decide(view, ctx) -> dict`. `HumanController` (queue fed by the
  transport), `PolicyController` / `ScriptedController` (tests, fallbacks)
  and the LLM `AgentController` (Phase 5) all satisfy it. The referee never
  branches on who fills a seat; `SeatSpec.kind` only sets the retry policy.
- Every turn: publish `game.turn_started`, build `player_view`, ask the
  controller, `parse_move` + `apply` as the validator. A refused move is
  published as `game.move_rejected` (seat-visible) with the reason and, for
  non-human seats, counts against `max_retries`; humans retry until the
  turn deadline. On exhaustion or timeout the referee plays
  `fallback_move` (the first legal move) and publishes
  `game.turn_timed_out` when a deadline caused it.
- After every move: `game.move_applied` (public, with `move_index` and a
  description) and one `game.view_updated` per seat (seat-visible) so a
  player client and the privileged focus view read the same stream.
- `Referee.replay(slug, seed, seat_count, moves)` rebuilds the state from
  the `move_applied` payloads; Phase 9 persistence relies on it.
- `GameRunner` owns one asyncio task per room, `stop()` cancels it and the
  referee publishes `room.stopped`; `submit_move()` feeds a human seat.
- Wide events: one `game` event per game and one `game.turn` per turn with
  seat, kind, model, attempts, retries, fallback and the applied move.

## Privacy gate (Phase 4)

One `player_view` function, two callers:

- `app.agents.prompt.build_move_prompt` / `build_chat_prompt` take a
  `PlayerView` and nothing else from the game (see `docs/agents.md`).
- `app.api.views.view_payload(game, state, seat)` is the one serialiser the
  HTTP/websocket layer uses for a human's own seat.

`tests/security/` treats the gate as a boundary: fuzzed prompts and a full
referee event stream are checked for any other seat's cards.
