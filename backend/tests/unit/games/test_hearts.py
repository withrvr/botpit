"""Hearts: contract suite plus scripted rule scenarios from hand-made states."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.games.base import IllegalMoveError
from app.games.cards import FrenchCard, Suit
from app.games.hearts import GAME
from app.games.hearts.game import deck_for, penalty
from app.games.hearts.moves import PassCards, Play
from app.games.hearts.state import HeartsState
from tests.unit.games import contract as c

_SUITS = {"C": Suit.CLUBS, "D": Suit.DIAMONDS, "H": Suit.HEARTS, "S": Suit.SPADES}
_RANKS = {"J": 11, "Q": 12, "K": 13, "A": 14}


def card(label: str) -> FrenchCard:
    rank = _RANKS.get(label[:-1]) or int(label[:-1])
    return FrenchCard.model_validate({"suit": _SUITS[label[-1]], "rank": rank})


def hand(labels: str) -> tuple[FrenchCard, ...]:
    return tuple(card(x) for x in labels.split())


def play_state(hands: list[str], **kw: Any) -> HeartsState:
    hs = tuple(hand(h) for h in hands)
    n = len(hs)
    base: dict[str, Any] = {
        "slug": "hearts",
        "seed": 0,
        "seat_count": n,
        "hands": hs,
        "scores": (0,) * n,
        "pending_passes": (None,) * n,
        "taken": ((),) * n,
        "phase": "play",
    }
    return HeartsState(**(base | kw))


def play(seat: int, label: str, state: HeartsState) -> Play:
    return Play(seat=seat, card_index=state.hands[seat].index(card(label)))


def labels(state: HeartsState, seat: int) -> list[str]:
    return [x.label() for x in state.hands[seat]]


@pytest.mark.parametrize("seats", [3, 4, 5, 6])
@pytest.mark.parametrize("check", c.ALL_CHECKS, ids=lambda f: f.__name__)
def test_contract(check: Any, seats: int) -> None:
    check(GAME, seats)


@pytest.mark.parametrize(
    ("seats", "size", "per_seat", "low_club"),
    [(3, 51, 17, "2C"), (4, 52, 13, "2C"), (5, 50, 10, "3C"), (6, 48, 8, "4C")],
)
def test_deck_and_deal(seats: int, size: int, per_seat: int, low_club: str) -> None:
    deck = deck_for(seats)
    assert len(deck) == size
    assert sum(penalty(x) for x in deck) == 26
    assert min(x.rank for x in deck if x.suit == Suit.CLUBS) == card(low_club).rank
    state = GAME.new_game(3, seats)
    assert [len(h) for h in state.hands] == [per_seat] * seats
    dealt = [x for h in state.hands for x in h]
    assert len(dealt) == size
    assert set(dealt) == set(deck)
    for h in state.hands:
        assert list(h) == sorted(h, key=lambda x: (x.suit, x.rank))
    assert state.phase == "pass"
    assert GAME.current_seat(state) == 0


def test_passing() -> None:
    state = GAME.new_game(1, 4)
    assert len(GAME.legal_moves(state, 0)) == 286  # C(13, 3)
    assert GAME.legal_moves(state, 1) == []
    with pytest.raises(IllegalMoveError, match="not your turn"):
        GAME.apply(state, PassCards(seat=1, cards=(0, 1, 2)))
    with pytest.raises(IllegalMoveError, match="distinct"):
        GAME.apply(state, PassCards(seat=0, cards=(0, 0, 2)))
    with pytest.raises(IllegalMoveError, match="out of range"):
        GAME.apply(state, PassCards(seat=0, cards=(0, 1, 13)))
    with pytest.raises(IllegalMoveError, match="pass_cards"):
        GAME.apply(state, Play(seat=0, card_index=0))
    assert PassCards(seat=0, cards=(5, 2, 9)).cards == (2, 5, 9)

    original = state.hands
    given = [state.hands[s][:3] for s in range(4)]
    for s in range(3):
        state = GAME.apply(state, PassCards(seat=s, cards=(0, 1, 2)))
        assert state.phase == "pass"
        assert state.hands == original
        assert GAME.current_seat(state) == s + 1
        for viewer in range(4):
            view = GAME.player_view(state, viewer)
            assert view.table == {"pass_direction": "left", "passes_done": s + 1}
            assert view.hand == [x.model_dump(mode="json") for x in original[viewer]]
    state = GAME.apply(state, PassCards(seat=3, cards=(0, 1, 2)))
    assert state.phase == "play"
    assert state.pending_passes == (None,) * 4
    for s in range(4):
        expected = set(original[s][3:]) | set(given[(s - 1) % 4])
        assert set(state.hands[s]) == expected
        assert list(state.hands[s]) == sorted(
            state.hands[s], key=lambda x: (x.suit, x.rank)
        )
    assert card("2C") in state.hands[state.leader]
    assert GAME.current_seat(state) == state.leader
    view = GAME.player_view(state, state.leader)
    assert view.phase == "play"
    assert view.table["trick"] == []
    assert view.table["led_suit"] is None


def test_first_lead_is_lowest_club() -> None:
    state = play_state(["2C 5D", "3C 4H", "9C QS"], leader=0)
    assert GAME.legal_moves(state, 0) == [Play(seat=0, card_index=0, card=card("2C"))]
    with pytest.raises(IllegalMoveError, match="first lead must be the 2C"):
        GAME.apply(state, play(0, "5D", state))
    state = play_state(["3C 5D", "4C 4H", "9C QS", "7C 8C", "AH KH"], leader=0)
    assert [m.card_index for m in GAME.legal_moves(state, 0)] == [0]


def test_must_follow_suit() -> None:
    taken = ((card("2C"),), (), ())
    state = play_state(
        ["5D", "3C 4H 9D", "9C QS"], leader=0, trick=(card("AC"),), taken=taken
    )
    assert GAME.current_seat(state) == 1
    assert [m.card_index for m in GAME.legal_moves(state, 1)] == [0]
    with pytest.raises(IllegalMoveError, match=r"follow suit \(clubs\)"):
        GAME.apply(state, play(1, "4H", state))
    with pytest.raises(IllegalMoveError, match="not 4H"):
        GAME.apply(state, Play(seat=1, card_index=0, card=card("4H")))
    with pytest.raises(IllegalMoveError, match="no such card"):
        GAME.apply(state, Play(seat=1, card_index=7))


def test_first_trick_restrictions() -> None:
    # Seat 1 is void in clubs on the first trick: only the diamond is allowed.
    state = play_state(["5D", "9D 4H QS", "9C 8S"], leader=0, trick=(card("2C"),))
    assert labels(state, 1) == ["9D", "4H", "QS"]
    assert [m.card_index for m in GAME.legal_moves(state, 1)] == [0]
    with pytest.raises(IllegalMoveError, match="first trick"):
        GAME.apply(state, play(1, "QS", state))
    # Nothing but penalty cards: anything goes.
    state = play_state(["5D", "4H QS", "9C 8S"], leader=0, trick=(card("2C"),))
    assert [m.card_index for m in GAME.legal_moves(state, 1)] == [0, 1]
    # Not the first trick any more: dump freely.
    taken = ((card("3C"),), (), ())
    state = play_state(
        ["5D", "9D 4H QS", "9C 8S"], leader=0, trick=(card("2C"),), taken=taken
    )
    assert [m.card_index for m in GAME.legal_moves(state, 1)] == [0, 1, 2]


def test_leading_hearts() -> None:
    taken = ((card("2C"),), (), ())
    state = play_state(["4H 9D", "5C", "6C"], leader=0, taken=taken)
    assert [m.card_index for m in GAME.legal_moves(state, 0)] == [1]
    with pytest.raises(IllegalMoveError, match="not broken"):
        GAME.apply(state, play(0, "4H", state))
    state = play_state(["4H 9D", "5C", "6C"], leader=0, taken=taken, hearts_broken=True)
    assert [m.card_index for m in GAME.legal_moves(state, 0)] == [0, 1]
    state = play_state(["4H 9H", "5C", "6C"], leader=0, taken=taken)
    assert [m.card_index for m in GAME.legal_moves(state, 0)] == [0, 1]


def test_trick_winner_and_rotation() -> None:
    taken = ((card("2C"), card("3C"), card("4C"), card("5C")), (), (), ())
    state = play_state(["9D 2S", "AH 3S", "KD 4S", "3D 5S"], leader=2, taken=taken)
    assert GAME.current_seat(state) == 2
    for seat, label in [(2, "KD"), (3, "3D"), (0, "9D"), (1, "AH")]:
        assert GAME.current_seat(state) == seat
        state = GAME.apply(state, play(seat, label, state))
    assert state.trick == ()
    assert state.leader == 2
    assert GAME.current_seat(state) == 2
    assert state.hearts_broken
    assert state.taken[2][-4:] == hand("KD 3D 9D AH")
    assert state.scores == (0, 0, 1, 0)
    view = GAME.player_view(state, 0)
    assert view.table["tricks_taken"] == [1, 0, 1, 0]
    assert view.table["penalty_so_far"] == [0, 0, 1, 0]
    assert view.table["trick_number"] == 2
    assert view.table["leader"] == 2
    assert not GAME.is_terminal(state)
    # Mid-trick view lists seat and card in play order.
    state = GAME.apply(state, play(2, "4S", state))
    view = GAME.player_view(state, 3)
    assert view.table["trick"] == [{"seat": 2, "card": {"suit": "spades", "rank": 4}}]
    assert view.table["led_suit"] == "spades"


def test_queen_of_spades_does_not_break_hearts_and_scores_13() -> None:
    taken = ((card("2C"),), (), ())
    state = play_state(["AS 4H", "QS 5C", "3S 6C"], leader=0, taken=taken)
    state = GAME.apply(state, play(0, "AS", state))
    state = GAME.apply(state, play(1, "QS", state))
    state = GAME.apply(state, play(2, "3S", state))
    assert not state.hearts_broken
    assert state.scores == (13, 0, 0)
    assert GAME.ranking(state) == [1, 2, 0]


def test_shoot_the_moon() -> None:
    twelve_hearts = hand("2H 3H 4H 5H 6H 7H 8H 9H 10H JH QH KH")
    taken = ((*twelve_hearts, card("QS")), (card("2C"),), (card("3C"),))
    state = play_state(["AH", "2S", "3S"], leader=0, taken=taken, hearts_broken=True)
    assert state.scores == (0, 0, 0)
    state = GAME.apply(state, play(0, "AH", state))
    state = GAME.apply(state, play(1, "2S", state))
    assert not GAME.is_terminal(state)
    state = GAME.apply(state, play(2, "3S", state))
    assert GAME.is_terminal(state)
    assert state.scores == (0, 26, 26)
    assert GAME.ranking(state) == [0, 1, 2]


def test_normal_scoring_and_tiebreak() -> None:
    taken = (
        (card("QS"), card("2H")),
        hand("3H 4H 5H 6H 7H 8H"),
        hand("9H 10H JH QH KH"),
    )
    state = play_state(
        ["2S", "AH", "3S"], leader=0, taken=taken, hearts_broken=True, scores=(14, 6, 5)
    )
    assert GAME.ranking(state) == [2, 1, 0]
    for seat, label in [(0, "2S"), (1, "AH"), (2, "3S")]:
        state = GAME.apply(state, play(seat, label, state))
    assert GAME.is_terminal(state)
    assert state.scores == (14, 6, 6)
    assert GAME.ranking(state) == [1, 2, 0]
    assert GAME.current_seat(state) is None
    assert GAME.legal_moves(state, 0) == []
    view = GAME.player_view(state, 1)
    assert view.terminal
    assert view.current_seat is None
    assert view.legal_moves == []
    with pytest.raises(IllegalMoveError):
        GAME.apply(state, Play(seat=0, card_index=0))


def test_describe_and_parse() -> None:
    assert (
        GAME.describe_move(PassCards(seat=1, cards=(0, 1, 2)))
        == "seat 1 passes 3 cards"
    )
    assert (
        GAME.describe_move(Play(seat=2, card_index=3, card=card("QS")))
        == "seat 2 plays QS"
    )
    assert GAME.describe_move(Play(seat=0, card_index=3)) == "seat 0 plays card 3"
    parsed = GAME.parse_move({"kind": "play", "seat": 2, "card_index": 3})
    assert parsed == Play(seat=2, card_index=3)
    assert GAME.parse_move(
        {"kind": "pass_cards", "seat": 1, "cards": [9, 0, 4]}
    ) == PassCards(seat=1, cards=(0, 4, 9))
    for bad in (
        {"kind": "draw", "seat": 0},
        {"kind": "play", "seat": 0},
        {"kind": "pass_cards", "seat": 0, "cards": [1, 2]},
        {"kind": "play", "seat": 0, "card_index": 1, "extra": 1},
    ):
        with pytest.raises(ValidationError):
            GAME.parse_move(bad)
    assert GAME.rules_markdown().startswith("# Hearts")
