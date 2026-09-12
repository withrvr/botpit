# Engine

Status: skeleton, filled in during Phases 2-4.

## Game module contract (Phase 2)

Every game is a module under `botpit/games/<name>/` exposing one class that
satisfies `botpit.games.base.Game`:

- `new_game(seed, seat_count) -> State`
- `legal_moves(state, seat) -> list[Move]`
- `apply(state, move) -> State` (pure, raises on illegal move)
- `is_terminal(state) -> bool`
- `ranking(state) -> list[int]` (seats, best first)
- `player_view(state, seat) -> PlayerView` (the privacy gate, §9)
- `rules_markdown() -> str` (handed to agents verbatim)
- `describe_move(move) -> str` (for logs and chat context)

Adding a game must not touch the referee, websocket layer, or frontend.

## Referee (Phase 3)

Deterministic. Owns turn order, validation, application, win detection.
Seats are abstract: human websocket moves and agent completions arrive as
the same validated move shape.

## Privacy gate (Phase 4)

One `player_view` function, two callers (agent prompt builder, human API).
