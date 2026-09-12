"""Contract every game must satisfy. Plain functions taking a game; each
game's test module parametrises over its seat range and calls them."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

import pytest

from botpit.games.base import GameState, IllegalMoveError, Move, PlayerView

if TYPE_CHECKING:
    from botpit.games.base import AnyGame

MAX_MOVES = 10000  # random UNO with 3-4 seats has been seen to run past 5000
PLAYOUT_SEEDS = 25


def random_playout(
    game: AnyGame, seat_count: int, seed: int, max_moves: int = MAX_MOVES
) -> list[tuple[GameState, Move]]:
    """Play random legal moves to the end. Returns (state_before, move) pairs."""
    rng = random.Random(seed)
    state = game.new_game(seed, seat_count)
    trace: list[tuple[GameState, Move]] = []
    while not game.is_terminal(state):
        seat = game.current_seat(state)
        assert seat is not None
        moves = game.legal_moves(state, seat)
        assert moves, f"no legal moves for seat {seat} in non-terminal state after {len(trace)}"
        move = rng.choice(moves)
        assert move.seat == seat
        try:
            nxt = game.apply(state, move)
        except IllegalMoveError as exc:  # pragma: no cover - contract violation
            pytest.fail(f"legal_moves offered a move apply rejected: {exc}")
        trace.append((state, move))
        state = nxt
        assert state.move_count == len(trace)
        if len(trace) > max_moves:
            pytest.fail(f"game did not terminate within {max_moves} moves")
    return trace


def replay(game: AnyGame, seat_count: int, seed: int, moves: list[Move]) -> GameState:
    state = game.new_game(seed, seat_count)
    for move in moves:
        state = game.apply(state, move)
    return state


def check_metadata(game: AnyGame, seat_count: int) -> None:
    assert game.slug
    assert game.min_seats <= seat_count <= game.max_seats
    assert game.rules_markdown().strip().startswith("#")
    state = game.new_game(1, seat_count)
    assert state.slug == game.slug
    assert state.seat_count == seat_count
    assert len(state.hands) == seat_count
    assert len(state.scores) == seat_count
    assert not game.is_terminal(state)
    with pytest.raises(ValueError, match="seats"):
        game.new_game(1, game.max_seats + 1)


def check_determinism(game: AnyGame, seat_count: int) -> None:
    trace = random_playout(game, seat_count, seed=7)
    moves = [m for _, m in trace]
    a = replay(game, seat_count, 7, moves)
    b = replay(game, seat_count, 7, moves)
    assert a == b
    assert a.model_dump() == b.model_dump()
    assert game.new_game(7, seat_count) != game.new_game(8, seat_count)


def check_serialisation(game: AnyGame, seat_count: int) -> None:
    trace = random_playout(game, seat_count, seed=11)
    sample = [trace[0][0], trace[len(trace) // 2][0], trace[-1][0]]
    state_type = type(sample[0])
    for state in sample:
        assert state_type.model_validate_json(state.model_dump_json()) == state
    for _, move in trace[:50]:
        parsed = game.parse_move(move.model_dump(mode="json"))
        assert parsed == move
        assert game.describe_move(move)
    view = game.player_view(sample[1], 0)
    assert PlayerView.model_validate_json(view.model_dump_json()) == view


def check_apply_rejects(game: AnyGame, seat_count: int) -> None:
    trace = random_playout(game, seat_count, seed=3)
    for state, move in trace[:40]:
        before = state.model_dump()
        other = (move.seat + 1) % seat_count
        with pytest.raises(IllegalMoveError):
            game.apply(state, move.model_copy(update={"seat": other}))
        assert state.model_dump() == before
        bad = game.legal_moves(state, other)
        assert bad == [] or all(m.seat == other for m in bad)
    # A move from a different game state is not blindly accepted.
    first_state, _ = trace[0]
    with pytest.raises(IllegalMoveError):
        game.apply(first_state, trace[-1][1].model_copy(update={"seat": 10**6}))


def check_progress_and_termination(game: AnyGame, seat_count: int) -> None:
    for seed in range(PLAYOUT_SEEDS):
        trace = random_playout(game, seat_count, seed=100 + seed)
        assert trace
        final = game.apply(trace[-1][0], trace[-1][1])
        assert game.is_terminal(final)
        assert game.current_seat(final) is None
        assert game.legal_moves(final, 0) == []
        with pytest.raises(IllegalMoveError):
            game.apply(final, trace[-1][1])


def check_ranking(game: AnyGame, seat_count: int) -> None:
    trace = random_playout(game, seat_count, seed=5)
    for state, _ in trace[::7]:
        assert sorted(game.ranking(state)) == list(range(seat_count))
    final = game.apply(trace[-1][0], trace[-1][1])
    assert sorted(game.ranking(final)) == list(range(seat_count))
    assert len(final.scores) == seat_count


def _permute_other_hands(state: GameState, seat: int, rng: random.Random) -> GameState:
    hands = list(state.hands)
    for other in range(state.seat_count):
        if other != seat and len(hands[other]) > 1:
            cards = list(hands[other])
            rng.shuffle(cards)
            if tuple(cards) == hands[other]:
                cards.reverse()
            hands[other] = tuple(cards)
    return state.model_copy(update={"hands": tuple(hands)})


def check_player_view_privacy(game: AnyGame, seat_count: int) -> None:
    """The view of seat s must not change when other seats' hands are reordered,
    must show exactly s's own hand and correct counts, and must only list s's moves."""
    rng = random.Random(0)
    trace = random_playout(game, seat_count, seed=9)
    for state, _ in trace[::5]:
        for seat in range(seat_count):
            view = game.player_view(state, seat)
            assert view.seat == seat
            assert view.slug == game.slug
            assert view.hand_counts == [len(h) for h in state.hands]
            assert view.hand == game.hand_view(state, seat)
            assert view.terminal == game.is_terminal(state)
            assert view.current_seat == game.current_seat(state)
            expected = [m.model_dump(mode="json") for m in game.legal_moves(state, seat)]
            assert view.legal_moves == expected
            if seat != view.current_seat:
                assert view.legal_moves == []
            shuffled = _permute_other_hands(state, seat, rng)
            assert game.player_view(shuffled, seat) == view


def check_illegal_from_view(game: AnyGame, seat_count: int) -> None:
    """A move a seat can build from another seat's view is rejected for the wrong seat."""
    trace = random_playout(game, seat_count, seed=13)
    state, move = trace[0]
    victim = (move.seat + 1) % seat_count
    data: dict[str, Any] = move.model_dump(mode="json") | {"seat": victim}
    with pytest.raises(IllegalMoveError):
        game.apply(state, game.parse_move(data))


ALL_CHECKS = (
    check_metadata,
    check_determinism,
    check_serialisation,
    check_apply_rejects,
    check_progress_and_termination,
    check_ranking,
    check_player_view_privacy,
    check_illegal_from_view,
)
