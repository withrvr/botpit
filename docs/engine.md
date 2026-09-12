# Engine

## Game module contract (Phase 2)

Every game is a package under `src/botpit/games/<slug>/` exposing a `GAME`
instance of `botpit.games.base.Game[State, Move]` and a `rules.md`. It is
registered by one line in `botpit/games/__init__.py` (`_GAMES`). Nothing
else changes when a game is added: the referee, websocket layer and
frontend only see the contract.

### Types (`botpit/games/base.py`)

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

## Referee (Phase 3)

Deterministic. Owns turn order, validation, application, win detection.
Seats are abstract: human websocket moves and agent completions arrive as
the same validated move shape.

## Privacy gate (Phase 4)

One `player_view` function, two callers (agent prompt builder, human API).
