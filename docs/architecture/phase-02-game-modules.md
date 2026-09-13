# Phase 2 — Game contract and the four v1 games

Status: done (PR review pending at time of writing in `PROGRESS.md`), 2026-09-12.

Historical snapshot on the pre-template codebase layout (`src/botpit/games/`
at the time; the equivalent code lives at `backend/app/games/` after Phase
2b). Not updated for later changes — see
`docs/architecture/latest_architecture.md` for the current module layout.

## What was built

- **`botpit.games.base`**: the `Game[State, Move]` contract every game
  package implements, plus `IllegalMoveError` and `PlayerView` (the privacy
  gate's output shape — see `docs/architecture/phase-04-privacy-gate.md` for
  the gate itself, built two phases later but designed against this same
  contract) and the `hand_view` hook a game overrides when a card holder
  must not see part of their own hand (UNO Flip's face-down cards).
  `GameState` is a frozen pydantic base (`slug`, `seed`, `seat_count`,
  `move_count`, `hands`, `scores`, `eliminated`); `Move` is a frozen
  pydantic base with a `kind` discriminator and a `seat`.
- **`cards.py`**: shared card types and `rng_for(state)` — a
  `random.Random` deterministically seeded from `(seed, move_count)`, so
  replaying a move list reproduces every shuffle with no generator state
  ever persisted.
- **`GAME_CATALOG`**: a registry populated by one line per game
  (`backend/app/games/__init__.py`'s `_GAMES` after Phase 2b's rename).
  Adding a game means dropping in a module and a `rules.md`; nothing else
  in the referee, transport, or frontend changes.
- **Four games, each with its own `rules.md` and scripted tests**, all
  passing one shared contract test suite (`tests/unit/games/contract.py`'s
  `ALL_CHECKS`, run per game across its supported seat range):

  | Slug | Title | Seats | Tests | Notes |
  |---|---|---|---|---|
  | `uno_classic` | Classic UNO | 2-10 | 92 | Wild Draw Four challenge, UNO catch window, draw-then-play as two moves |
  | `uno_flip` | UNO Flip! | 2-10 | 96 | double-faced cards, light/dark sides, `hand_view` shows only the active face |
  | `uno_no_mercy` | UNO Show 'em No Mercy | 2-10 | 104 | stacking, 7/0 rules, mercy elimination at 25 cards |
  | `hearts` | Hearts | 3-6 | 46 | sequential hidden passing, follow-suit, shoot-the-moon; the one structurally different (trick-taking) v1 game per `DECISIONS.md` §4 |

  Plus catalog round-trip tests. **Hearts proved the contract generalizes**:
  no change to the base contract or registry mechanism was needed beyond
  one registry line, despite Hearts being structurally unlike any UNO
  variant (tricks, a passing phase, lowest-score-wins instead of
  last-player-standing).

## Key implementation decisions made during this phase

These are contract-shape decisions baked in while building the four games,
distinct from `DECISIONS.md`'s pre-registered numbered decisions (§4 covers
the contract's existence and rationale; the specifics below are what filled
it in during implementation):

- **Randomness is derived, never stored.** `cards.rng_for(state)` reseeds
  from `(seed, move_count)` rather than persisting RNG state — this is what
  makes `Referee.replay` (Phase 3) possible at all: replaying the exact
  move sequence reproduces every shuffle byte-for-byte.
- **Wild colour choice is part of the play move**, never a separate second
  move — one less round-trip for both human and agent seats, and one fewer
  intermediate state to define `legal_moves` for.
- **Draw-then-play is two moves** (`draw`, then play the drawn card or
  `pass`), not one atomic "draw and maybe play" move — the intermediate
  state (holding an undrawn-for card, deciding whether to play it) is a
  real decision point worth exposing to an agent's `legal_moves`, not
  hidden inside one compound action.
- **UNO-call is a flag on the play move**; catching a missed call is a move
  available to the current seat that does not consume their turn — this
  keeps the turn-order state machine simple (exactly one seat has a
  pending decision at a time) without losing the catch mechanic.
- **Single round per game in v1**; `scores` already carries round points so
  multi-round play is additive future work, not a schema change.
- **Elimination ranking** (No Mercy): eliminated seats rank below
  survivors, last-eliminated ranked highest among the eliminated; ties
  break by seat index. This had to be decided somewhere, since `ranking`
  is defined at every state including mid-elimination ones.

## Contract shape introduced

```
backend/app/games/
├── base.py         # Game[State, Move], GameState, Move, PlayerView, IllegalMoveError
├── cards.py         # shared card types, rng_for(state)
├── __init__.py      # GAME_CATALOG / _GAMES registry
├── uno_classic/
│   ├── __init__.py  # GAME instance
│   └── rules.md
├── uno_flip/
├── uno_no_mercy/
└── hearts/
```

Every game exposes `new_game(seed, seat_count)`, `parse_move(dict)`,
`current_seat(state)`, `legal_moves(state, seat)`, `apply(state, move)`,
`ranking(state)`, `player_view(state, seat)`, `rules_markdown()`,
`describe_move(move)`, and a `move_adapter` class var (a `TypeAdapter` over
the game's discriminated move union) — the full contract is documented in
`docs/engine.md`, which is a living document (not a phase snapshot) and
should be read for the current, complete version of these method contracts.

## Tests and what was verified

Contract tests (`tests/unit/games/contract.py`) run identically across
every game and every seat count it supports: metadata correctness,
determinism (same seed + same moves = same state), serialization
round-trips for state/move/view, rejection of wrong-seat and stale moves,
random playouts terminating across many seeds, `ranking` always being a
valid permutation of every seat, and `player_view` privacy (a seat sees
only its own hand, correct opponent hand *counts*, only its own legal
moves, and reordering other seats' hands leaves its own view
byte-identical — the earliest version of the privacy-gate testing
discipline that Phase 4 later formalizes as a dedicated security suite).

Total suite at the end of this phase: **458 tests, ~18 seconds.**
