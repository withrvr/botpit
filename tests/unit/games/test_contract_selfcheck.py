"""Proves the contract suite itself works, using a toy game: each seat plays
one card per turn from a dealt hand; the seat with the highest total wins."""

from __future__ import annotations

from typing import Any, Literal

import pytest

from botpit.games.base import Game, GameState, IllegalMoveError, Move, PlayerView, Seat
from botpit.games.cards import FrenchCard, deal, rng_for_seed, shuffled, standard_52
from tests.unit.games import contract as c


class Play(Move):
    kind: Literal["play"] = "play"
    index: int


class HighState(GameState):
    hands: tuple[tuple[FrenchCard, ...], ...]
    turn: Seat
    played: tuple[int, ...]


class HighCard(Game[HighState, Play]):
    slug = "highcard"
    title = "High Card"
    min_seats = 2
    max_seats = 4

    def new_game(self, seed: int, seat_count: int) -> HighState:
        self.check_seat_count(seat_count)
        hands, _ = deal(shuffled(rng_for_seed(seed), standard_52()), seat_count, 3)
        return HighState(
            slug=self.slug,
            seed=seed,
            seat_count=seat_count,
            hands=hands,
            scores=(0,) * seat_count,
            turn=0,
            played=(0,) * seat_count,
        )

    def parse_move(self, data: dict[str, Any]) -> Play:
        return Play.model_validate(data)

    def current_seat(self, state: HighState) -> Seat | None:
        return None if all(len(h) == 0 for h in state.hands) else state.turn

    def legal_moves(self, state: HighState, seat: Seat) -> list[Play]:
        if seat != self.current_seat(state):
            return []
        return [Play(seat=seat, index=i) for i in range(len(state.hands[seat]))]

    def apply(self, state: HighState, move: Play) -> HighState:
        if move.seat != self.current_seat(state):
            raise IllegalMoveError(move, "not your turn")
        if not 0 <= move.index < len(state.hands[move.seat]):
            raise IllegalMoveError(move, "no such card")
        hand = list(state.hands[move.seat])
        card = hand.pop(move.index)
        hands = list(state.hands)
        hands[move.seat] = tuple(hand)
        played = list(state.played)
        played[move.seat] += card.rank
        return state.model_copy(
            update={
                "hands": tuple(hands),
                "played": tuple(played),
                "turn": (state.turn + 1) % state.seat_count,
                "move_count": state.move_count + 1,
                "scores": tuple(played),
            }
        )

    def ranking(self, state: HighState) -> list[Seat]:
        return sorted(range(state.seat_count), key=lambda s: (-state.played[s], s))

    def player_view(self, state: HighState, seat: Seat) -> PlayerView:
        return PlayerView(
            slug=self.slug,
            seat=seat,
            phase="play",
            current_seat=self.current_seat(state),
            direction=1,
            hand=[c.model_dump(mode="json") for c in state.hands[seat]],
            hand_counts=[len(h) for h in state.hands],
            table={"played": list(state.played)},
            legal_moves=[m.model_dump(mode="json") for m in self.legal_moves(state, seat)],
            scores=list(state.scores),
            eliminated=[],
            terminal=self.is_terminal(state),
        )

    def rules_markdown(self) -> str:
        return "# High Card\nPlay a card; highest total wins."

    def describe_move(self, move: Play) -> str:
        return f"seat {move.seat} plays card {move.index}"


GAME = HighCard()


@pytest.mark.parametrize("seats", [2, 3, 4])
@pytest.mark.parametrize("check", c.ALL_CHECKS, ids=lambda f: f.__name__)
def test_contract(check: Any, seats: int) -> None:
    check(GAME, seats)
