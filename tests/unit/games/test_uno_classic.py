from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from botpit.games.base import IllegalMoveError
from botpit.games.cards import Colour, UnoCard
from botpit.games.uno_classic import GAME, UnoState
from botpit.games.uno_classic.game import build_deck
from botpit.games.uno_classic.moves import (
    AcceptDraw4,
    ChallengeDraw4,
    ChallengeUno,
    Draw,
    Pass,
    Play,
)
from tests.unit.games import contract as c

RED, YELLOW, GREEN, BLUE = Colour.RED, Colour.YELLOW, Colour.GREEN, Colour.BLUE


def card(colour: Colour | None, rank: str) -> UnoCard:
    return UnoCard(colour=colour, rank=rank)


R3, R7, R9, B7, G9, Y2 = (
    card(RED, "3"),
    card(RED, "7"),
    card(RED, "9"),
    card(BLUE, "7"),
    card(GREEN, "9"),
    card(YELLOW, "2"),
)
WILD, WD4 = card(None, "wild"), card(None, "wild_draw4")


def make(
    hands: list[list[UnoCard]],
    top: UnoCard,
    draw: list[UnoCard] | None = None,
    active: Colour | None = None,
    **extra: Any,
) -> UnoState:
    n = len(hands)
    return UnoState(
        slug="uno_classic",
        seed=1,
        seat_count=n,
        hands=tuple(tuple(h) for h in hands),
        scores=(0,) * n,
        draw_pile=tuple(draw if draw is not None else [Y2] * 12),
        discard_pile=(top,),
        active_colour=active or top.colour,
        uno_called=(True,) * n,
        **extra,
    )


def kinds(state: UnoState, seat: int) -> set[str]:
    return {m.kind for m in GAME.legal_moves(state, seat)}


@pytest.mark.parametrize("seats", range(2, 11))
@pytest.mark.parametrize("check", c.ALL_CHECKS, ids=lambda f: f.__name__)
def test_contract(check: Any, seats: int) -> None:
    check(GAME, seats)


def test_deck_composition() -> None:
    deck = build_deck()
    assert len(deck) == 108
    by_rank = Counter(cd.rank for cd in deck)
    assert by_rank["0"] == 4
    assert all(by_rank[str(n)] == 8 for n in range(1, 10))
    assert by_rank["skip"] == by_rank["reverse"] == by_rank["draw2"] == 8
    assert by_rank["wild"] == by_rank["wild_draw4"] == 4
    assert Counter(cd.colour for cd in deck) == {RED: 25, YELLOW: 25, GREEN: 25, BLUE: 25, None: 8}


def test_first_discard_effects() -> None:
    seen: dict[str, UnoState] = {}
    for seed in range(400):
        state = GAME.new_game(seed, 3)
        top = state.top
        assert top.rank != "wild_draw4"
        assert sum(len(h) for h in state.hands) + len(state.draw_pile) + 1 == 108
        assert Counter(state.draw_pile + sum(state.hands, ()))[WD4] == 4  # put back, not lost
        seen.setdefault("number" if top.is_number else top.rank, state)
    assert set(seen) == {"number", "skip", "reverse", "draw2", "wild"}
    assert (seen["number"].turn, seen["number"].active_colour) == (0, seen["number"].top.colour)
    assert seen["skip"].turn == 1
    assert (seen["reverse"].direction, seen["reverse"].turn) == (-1, 0)
    assert (len(seen["draw2"].hands[0]), seen["draw2"].turn) == (9, 1)
    assert (seen["wild"].active_colour, seen["wild"].turn) == (None, 0)
    two = next(GAME.new_game(s, 2) for s in range(400) if GAME.new_game(s, 2).top.rank == "reverse")
    assert two.turn == 1  # reverse acts as skip with two seats


def test_first_discard_wild_any_card_matches() -> None:
    state = make([[G9, B7], [R3]], WILD, active=None)
    assert {m.card_index for m in GAME.legal_moves(state, 0) if isinstance(m, Play)} == {0, 1}
    after = GAME.apply(state, Play(seat=0, card_index=0))
    assert after.active_colour == GREEN


def test_matching_colour_number_symbol() -> None:
    state = make([[R3, B7, G9, WILD], [Y2]], R7)
    plays = [m for m in GAME.legal_moves(state, 0) if isinstance(m, Play)]
    assert {m.card_index for m in plays} == {0, 1, 3}
    assert sum(m.card_index == 3 for m in plays) == 4  # one per declared colour
    with pytest.raises(IllegalMoveError, match="matches neither"):
        GAME.apply(state, Play(seat=0, card_index=2))
    skip = make([[card(BLUE, "skip")], [Y2, Y2]], card(RED, "skip"))
    assert kinds(skip, 0) >= {"play"}


def test_skip() -> None:
    state = make([[card(RED, "skip"), R3], [Y2], [Y2]], R7)
    after = GAME.apply(state, Play(seat=0, card_index=0))
    assert after.turn == 2
    assert after.discard_pile[-1] == card(RED, "skip")


def test_reverse_three_and_two_seats() -> None:
    rev = card(RED, "reverse")
    three = GAME.apply(make([[rev, R3], [Y2], [Y2]], R7), Play(seat=0, card_index=0))
    assert (three.direction, three.turn) == (-1, 2)
    two = GAME.apply(make([[rev, R3], [Y2]], R7), Play(seat=0, card_index=0))
    assert (two.direction, two.turn) == (-1, 0)


def test_draw_two() -> None:
    state = make([[card(RED, "draw2"), R3], [Y2], [Y2]], R7)
    after = GAME.apply(state, Play(seat=0, card_index=0))
    assert len(after.hands[1]) == 3
    assert after.turn == 2
    assert len(after.draw_pile) == len(state.draw_pile) - 2


def test_wild_needs_declared_colour() -> None:
    state = make([[WILD, R3], [Y2]], R7)
    with pytest.raises(IllegalMoveError, match="declare"):
        GAME.apply(state, Play(seat=0, card_index=0))
    with pytest.raises(IllegalMoveError, match="declare"):
        GAME.apply(state, Play(seat=0, card_index=0, declared_colour=Colour.PINK))
    after = GAME.apply(state, Play(seat=0, card_index=0, declared_colour=BLUE))
    assert after.active_colour == BLUE
    assert after.turn == 1
    with pytest.raises(IllegalMoveError, match="only wilds"):
        GAME.apply(state, Play(seat=0, card_index=1, declared_colour=BLUE))


def test_wild_draw4_challenge_succeeds_when_colour_held() -> None:
    state = make([[WD4, R3], [Y2], [Y2]], R7)
    pending = GAME.apply(state, Play(seat=0, card_index=0, declared_colour=GREEN))
    assert (pending.phase, pending.turn, pending.active_colour) == ("draw4_response", 1, GREEN)
    assert pending.draw4_prev_colour == RED
    assert GAME.legal_moves(pending, 1) == [AcceptDraw4(seat=1), ChallengeDraw4(seat=1)]
    assert GAME.player_view(pending, 1).table["pending_draw"] == 4
    with pytest.raises(IllegalMoveError):
        GAME.apply(pending, Draw(seat=1))
    after = GAME.apply(pending, ChallengeDraw4(seat=1))
    assert len(after.hands[0]) == 5  # R3 + 4 penalty
    assert len(after.hands[1]) == 1
    assert (after.phase, after.turn, after.active_colour) == ("play", 1, GREEN)


def test_wild_draw4_challenge_fails_when_legal() -> None:
    state = make([[WD4, B7], [Y2], [Y2]], R7)
    pending = GAME.apply(state, Play(seat=0, card_index=0, declared_colour=GREEN))
    after = GAME.apply(pending, ChallengeDraw4(seat=1))
    assert len(after.hands[0]) == 1
    assert len(after.hands[1]) == 7
    assert (after.phase, after.turn) == ("play", 2)


def test_accept_draw4() -> None:
    state = make([[WD4, R3], [Y2], [Y2]], R7)
    pending = GAME.apply(state, Play(seat=0, card_index=0, declared_colour=GREEN))
    after = GAME.apply(pending, AcceptDraw4(seat=1))
    assert len(after.hands[1]) == 5
    assert (after.phase, after.turn, after.draw4_player) == ("play", 2, None)


def test_draw_then_play_playable() -> None:
    state = make([[G9, card(GREEN, "4")], [Y2]], R7, draw=[R3])
    drawn = GAME.apply(state, Draw(seat=0))
    assert drawn.phase == "after_draw"
    assert drawn.hands[0] == (G9, card(GREEN, "4"), R3)
    assert GAME.player_view(drawn, 0).table["drawn_card"] == {"colour": "red", "rank": "3"}
    assert "drawn_card" not in GAME.player_view(drawn, 1).table
    assert GAME.legal_moves(drawn, 0) == [Play(seat=0, card_index=2, card=R3), Pass(seat=0)]
    with pytest.raises(IllegalMoveError, match="drawn card"):
        GAME.apply(drawn, Play(seat=0, card_index=0))
    with pytest.raises(IllegalMoveError, match="already drew"):
        GAME.apply(drawn, Draw(seat=0))
    played = GAME.apply(drawn, Play(seat=0, card_index=2))
    assert (len(played.hands[0]), played.turn, played.phase) == (2, 1, "play")
    passed = GAME.apply(drawn, Pass(seat=0))
    assert (len(passed.hands[0]), passed.turn, passed.phase) == (3, 1, "play")


def test_draw_then_pass_only_when_unplayable() -> None:
    state = make([[G9], [Y2]], R7, draw=[card(BLUE, "4")])
    drawn = GAME.apply(state, Draw(seat=0))
    assert GAME.legal_moves(drawn, 0) == [Pass(seat=0)]


def test_pass_illegal_outside_after_draw() -> None:
    state = make([[G9], [Y2]], R7)
    assert "pass" not in kinds(state, 0)
    with pytest.raises(IllegalMoveError, match="after drawing"):
        GAME.apply(state, Pass(seat=0))


def test_uno_call_and_challenge_window() -> None:
    state = make([[R3, G9], [Y2, Y2], [Y2, Y2]], R7)
    assert {m.called_uno for m in GAME.legal_moves(state, 0) if isinstance(m, Play)} == {
        True,
        False,
    }
    forgot = GAME.apply(state, Play(seat=0, card_index=0))
    assert GAME.player_view(forgot, 1).table["uno_pending"] == [0]
    assert ChallengeUno(seat=1, target=0) in GAME.legal_moves(forgot, 1)
    assert ChallengeUno(seat=2, target=0) not in GAME.legal_moves(forgot, 2)
    caught = GAME.apply(forgot, ChallengeUno(seat=1, target=0))
    assert len(caught.hands[0]) == 3
    assert (caught.turn, caught.move_count) == (1, forgot.move_count + 1)
    assert "challenge_uno" not in kinds(caught, 1)
    closed = GAME.apply(forgot, Draw(seat=1))
    assert GAME.player_view(closed, 1).table["uno_pending"] == []
    with pytest.raises(IllegalMoveError, match="cannot be caught"):
        GAME.apply(GAME.apply(closed, Pass(seat=1)), ChallengeUno(seat=2, target=0))
    called = GAME.apply(state, Play(seat=0, card_index=0, called_uno=True))
    assert "challenge_uno" not in kinds(called, 1)
    with pytest.raises(IllegalMoveError, match="one card"):
        GAME.apply(make([[R3, G9, B7], [Y2]], R7), Play(seat=0, card_index=0, called_uno=True))


def test_uno_window_survives_draw4_response() -> None:
    state = make([[WD4, R3], [Y2], [Y2]], R7)
    pending = GAME.apply(state, Play(seat=0, card_index=0, declared_colour=GREEN))
    accepted = GAME.apply(pending, AcceptDraw4(seat=1))
    assert ChallengeUno(seat=2, target=0) in GAME.legal_moves(accepted, 2)


def test_reshuffle_when_draw_pile_empties() -> None:
    state = make([[G9], [Y2]], R7, draw=[]).model_copy(
        update={"discard_pile": (B7, R3, card(BLUE, "1"), R7)}
    )
    after = GAME.apply(state, Draw(seat=0))
    assert after.discard_pile == (R7,)
    assert len(after.draw_pile) == 2
    assert Counter(after.draw_pile + after.hands[0]) == Counter((B7, R3, card(BLUE, "1"), G9))
    assert GAME.player_view(after, 1).table["draw_pile_count"] == 2


def test_terminal_scoring_and_ranking() -> None:
    state = make([[card(RED, "draw2")], [Y2, WILD], [G9], [R3, card(GREEN, "skip")]], R7)
    final = GAME.apply(state, Play(seat=0, card_index=0))
    assert final.terminal
    assert final.winner == 0
    assert GAME.current_seat(final) is None
    assert len(final.hands[1]) == 4  # final Draw Two still lands
    assert final.scores[0] == 0
    assert final.scores[1] == 2 + 50 + 2 + 2
    assert final.scores[2] == 9
    assert final.scores[3] == 3 + 20
    assert GAME.ranking(final) == [0, 2, 3, 1]
    assert GAME.legal_moves(final, 1) == []
    with pytest.raises(IllegalMoveError):
        GAME.apply(final, Draw(seat=1))
    # winner ranks first even when another seat also has zero points
    tie = make([[card(RED, "0")], [card(BLUE, "0")]], R7).model_copy(
        update={"terminal": True, "winner": 1, "hands": ((card(RED, "0"),), ())}
    )
    assert GAME.ranking(tie) == [1, 0]


def test_no_progress_termination() -> None:
    # seat 0 just drew and passes; seat 1 cannot play blue 4 on red 7 and nothing is drawable
    state = make([[R3, G9], [card(BLUE, "4")], [Y2]], R7, draw=[], phase="after_draw")
    final = GAME.apply(state, Pass(seat=0))
    assert final.terminal
    assert final.winner is None
    assert GAME.current_seat(final) is None
    assert final.scores == (12, 4, 2)
    assert GAME.ranking(final) == [2, 1, 0]
    # with a drawable card the game goes on
    alive = GAME.apply(state.model_copy(update={"draw_pile": (B7,)}), Pass(seat=0))
    assert not alive.terminal
    assert GAME.legal_moves(alive, 1) == [Draw(seat=1)]


def test_describe_move() -> None:
    assert GAME.describe_move(Play(seat=2, card_index=1, card=R7)) == "seat 2 plays red 7"
    assert (
        GAME.describe_move(Play(seat=0, card_index=0, declared_colour=BLUE, card=WILD))
        == "seat 0 plays wild (chooses blue)"
    )
    assert GAME.describe_move(Play(seat=1, card_index=3)) == "seat 1 plays card 3"
    assert GAME.describe_move(Draw(seat=3)) == "seat 3 draws a card"
    assert GAME.describe_move(ChallengeDraw4(seat=1)) == "seat 1 challenges the Wild Draw Four"
    assert GAME.parse_move({"kind": "challenge_uno", "seat": 0, "target": 2}) == ChallengeUno(
        seat=0, target=2
    )
    with pytest.raises(ValueError, match="kind"):
        GAME.parse_move({"kind": "steal", "seat": 0})
