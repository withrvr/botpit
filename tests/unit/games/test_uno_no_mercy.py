from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from botpit.games import GAME_CATALOG
from botpit.games.base import IllegalMoveError
from botpit.games.cards import Colour, UnoCard
from botpit.games.uno_no_mercy import GAME
from botpit.games.uno_no_mercy.game import build_deck
from botpit.games.uno_no_mercy.moves import ChallengeUno, Draw, Pass, Play
from botpit.games.uno_no_mercy.state import NoMercyState
from tests.unit.games import contract as c


@pytest.mark.parametrize("seats", range(2, 11))
@pytest.mark.parametrize("check", c.ALL_CHECKS, ids=lambda f: f.__name__)
def test_contract(check: Any, seats: int) -> None:
    check(GAME, seats)


def test_registered() -> None:
    assert GAME_CATALOG["uno_no_mercy"] is GAME
    assert GAME.title == "UNO Show 'em No Mercy"


# -- helpers ------------------------------------------------------------------


def card(text: str) -> UnoCard:
    """``"red 5"`` or ``"wild_draw6"``."""
    parts = text.split()
    if len(parts) == 1:
        return UnoCard(colour=None, rank=parts[0])
    return UnoCard(colour=Colour(parts[0]), rank=parts[1])


def cards(*texts: str) -> tuple[UnoCard, ...]:
    return tuple(card(t) for t in texts)


FILLER = ("green 3", "green 4", "green 5")


def make(
    *hands: tuple[UnoCard, ...] | None,
    top: str = "blue 1",
    draw_pile: tuple[UnoCard, ...] = cards("yellow 9", "yellow 8", "yellow 7", "yellow 6"),
    **extra: Any,
) -> NoMercyState:
    """Hand-made state; ``None`` hands get filler so no seat is accidentally on 1 card."""
    full = tuple(cards(*FILLER) if h is None else h for h in hands)
    t = card(top)
    return NoMercyState(
        slug=GAME.slug,
        seed=0,
        seat_count=len(full),
        hands=full,
        scores=(0,) * len(full),
        draw_pile=draw_pile,
        discard_pile=(t,),
        active_colour=extra.pop("active_colour", t.colour or Colour.BLUE),
        **extra,
    )


def play(state: NoMercyState, index: int, **kw: Any) -> NoMercyState:
    seat = GAME.current_seat(state)
    assert seat is not None
    return GAME.apply(state, Play(seat=seat, card_index=index, **kw))


def draw(state: NoMercyState) -> NoMercyState:
    seat = GAME.current_seat(state)
    assert seat is not None
    return GAME.apply(state, Draw(seat=seat))


def kinds(state: NoMercyState) -> set[str]:
    seat = GAME.current_seat(state)
    assert seat is not None
    return {m.kind for m in GAME.legal_moves(state, seat)}


# -- deck and setup -------------------------------------------------------------


def test_deck_counts() -> None:
    deck = build_deck()
    assert len(deck) == 168
    by_rank = Counter(c.rank for c in deck)
    for n in range(10):
        assert by_rank[str(n)] == 8
    assert by_rank["skip"] == by_rank["reverse"] == by_rank["draw2"] == 8
    assert by_rank["draw4"] == by_rank["skip_all"] == by_rank["discard_all"] == 16
    for wild in ("wild_reverse_draw4", "wild_draw6", "wild_draw10", "wild_roulette"):
        assert by_rank[wild] == 4
        assert all(c.colour is None for c in deck if c.rank == wild)
    assert "wild" not in by_rank
    assert "wild_draw4" not in by_rank
    assert Counter(c.colour for c in deck)[Colour.RED] == 38


def test_new_game_deal_and_first_discard() -> None:
    for seed in range(40):
        s = GAME.new_game(seed, 4)
        assert all(len(h) == 7 for h in s.hands)
        assert s.top.is_number
        assert s.active_colour == s.top.colour
        assert len(s.draw_pile) + len(s.discard_pile) + 28 == 168
        assert s.scores == tuple(
            sum(int(c.rank) if c.is_number else 50 if c.is_wild else 20 for c in h) for h in s.hands
        )
    # some seed turns up action cards first: they go under the pile in order
    seeds = [seed for seed in range(200) if not GAME.new_game(seed, 2).draw_pile[-1].is_number]
    assert seeds
    s = GAME.new_game(seeds[0], 2)
    assert not s.draw_pile[-1].is_number


# -- matching -----------------------------------------------------------------


def test_matching_by_colour_number_symbol_and_wild() -> None:
    s = make(cards("blue 5", "red 1", "red skip", "wild_draw6", "red 9"), None, top="blue 1")
    legal = {m.card_index for m in GAME.legal_moves(s, 0) if isinstance(m, Play)}
    assert legal == {0, 1, 3}
    with pytest.raises(IllegalMoveError, match="matches neither"):
        play(s, 4)
    s2 = make(cards("red skip", "blue 5"), None, top="green skip")
    assert {m.card_index for m in GAME.legal_moves(s2, 0) if isinstance(m, Play)} == {0}


def test_wild_needs_colour_and_only_wilds_take_colour() -> None:
    s = make(cards("wild_draw6", "blue 5"), None)
    with pytest.raises(IllegalMoveError, match="declared_colour"):
        play(s, 0)
    with pytest.raises(IllegalMoveError, match="only for wild"):
        play(s, 1, declared_colour=Colour.RED)
    s2 = play(s, 0, declared_colour=Colour.RED)
    assert s2.active_colour == Colour.RED
    assert s2.pending_draw == 6


def test_card_field_checked_when_supplied() -> None:
    s = make(cards("blue 5", "blue 6"), None)
    with pytest.raises(IllegalMoveError, match="is blue 5, not blue 6"):
        play(s, 0, card=card("blue 6"))
    assert play(s, 0, card=card("blue 5")).top == card("blue 5")


def test_draw_then_play_or_pass() -> None:
    s = make(cards("red 5", "red 6"), None, draw_pile=cards("blue 9", "green 2"))
    assert kinds(s) == {"draw"}
    s = draw(s)
    assert s.phase == "after_draw"
    assert GAME.current_seat(s) == 0
    assert GAME.player_view(s, 0).table["drawn_card"] == {"colour": "blue", "rank": "9"}
    assert "drawn_card" not in GAME.player_view(s, 1).table
    assert kinds(s) == {"play", "pass"}
    with pytest.raises(IllegalMoveError, match="drawn card"):
        play(s, 0)
    with pytest.raises(IllegalMoveError, match="already drew"):
        draw(s)
    s2 = play(s, 2)
    assert s2.top == card("blue 9")
    assert GAME.current_seat(s2) == 1
    s3 = draw(make(cards("red 5"), None, draw_pile=cards("green 2", "blue 4")))
    assert kinds(s3) == {"pass"}
    s3 = GAME.apply(s3, Pass(seat=0))
    assert GAME.current_seat(s3) == 1
    with pytest.raises(IllegalMoveError, match="pass"):
        GAME.apply(s3, Pass(seat=1))


# -- action cards -------------------------------------------------------------


def test_skip_reverse_and_skip_everyone() -> None:
    s = make(cards("blue skip", "blue reverse", "blue skip_all", "blue 2"), None, None)
    assert GAME.current_seat(play(s, 0)) == 2
    r = play(s, 1)
    assert r.direction == -1
    assert GAME.current_seat(r) == 2
    assert GAME.current_seat(play(s, 2)) == 0
    assert GAME.current_seat(play(s, 3)) == 1


def test_reverse_with_two_seats_acts_as_skip() -> None:
    s = make(cards("blue reverse", "blue 2"), None)
    after = play(s, 0)
    assert GAME.current_seat(after) == 0
    assert after.direction == -s.direction  # direction still flips, as in the other UNO games


def test_draw_two_and_draw_four_set_pending() -> None:
    s = make(cards("blue draw2", "blue draw4"), None, None)
    s2 = play(s, 1)
    assert s2.pending_draw == 4
    assert s2.phase == "draw_response"
    assert GAME.current_seat(s2) == 1


def test_discard_all_takes_colour_along_and_can_win() -> None:
    s = make(cards("blue discard_all", "blue 3", "red 4", "blue 9"), None)
    s2 = play(s, 0)
    assert s2.hands[0] == cards("red 4")
    assert s2.top == card("blue discard_all")
    assert s2.discard_pile[-3:] == cards("blue 3", "blue 9", "blue discard_all")
    assert s2.uno_pending == 0
    w = play(make(cards("blue discard_all", "blue 3", "blue 9"), None), 0)
    assert w.winner == 0
    assert GAME.is_terminal(w)
    assert GAME.ranking(w)[0] == 0
    # symbol match: a Discard All on a Discard All of another colour
    s3 = make(cards("red discard_all", "red 1", "blue 2"), None, top="blue discard_all")
    assert play(s3, 0).hands[0] == cards("blue 2")


def test_seven_swaps_hands_with_required_target() -> None:
    s = make(cards("blue 7", "blue 3"), cards("red 1"), cards("red 2", "red 3"), eliminated=(2,))
    with pytest.raises(IllegalMoveError, match="target_seat"):
        play(s, 0)
    with pytest.raises(IllegalMoveError, match="cannot swap"):
        play(s, 0, target_seat=0)
    with pytest.raises(IllegalMoveError, match="cannot swap"):
        play(s, 0, target_seat=2)
    with pytest.raises(IllegalMoveError, match="target_seat is only"):
        play(s, 1, target_seat=1)
    targets = {m.target_seat for m in GAME.legal_moves(s, 0) if isinstance(m, Play)}
    assert targets == {None, 1}
    s2 = play(s, 0, target_seat=1)
    assert s2.hands[0] == cards("red 1")
    assert s2.hands[1] == cards("blue 3")
    assert GAME.current_seat(s2) == 1


def test_seven_as_last_card_wins_without_swap() -> None:
    s = make(cards("blue 7"), cards("red 1"))
    s2 = play(s, 0, target_seat=1)
    assert s2.winner == 0
    assert s2.hands == ((), cards("red 1"))


def test_zero_rotates_hands_both_directions_and_skips_eliminated() -> None:
    a, b, cc, d = cards("blue 0", "blue 1"), cards("red 1"), cards("red 2"), cards("red 3")
    s = make(a, b, cc, d, eliminated=(2,))
    s2 = play(s, 0)
    assert s2.hands == (cards("red 3"), cards("blue 1"), cards("red 2"), cards("red 1"))
    s3 = play(s.model_copy(update={"direction": -1}), 0)
    assert s3.hands == (cards("red 1"), cards("red 3"), cards("red 2"), cards("blue 1"))
    assert GAME.current_seat(s3) == 3


# -- stacking -----------------------------------------------------------------


def test_stacking_chain_equal_higher_and_refusal() -> None:
    s = make(
        cards("blue draw2", "blue 1", "blue 2"),
        cards("red draw2", "red 1", "red 2"),
        cards("wild_draw6", "red 1", "red 2"),
        cards("red 1", "red 2"),
        draw_pile=cards(*["yellow 1"] * 12),
    )
    s = play(s, 0)
    assert s.pending_draw == 2
    assert kinds(s) == {"play", "draw"}
    # equal value stacks, colour does not matter
    s = play(s, 0)
    assert s.pending_draw == 4
    assert s.active_colour == Colour.RED
    assert GAME.current_seat(s) == 2
    s = play(s, 0, declared_colour=Colour.GREEN)
    assert s.pending_draw == 10
    assert GAME.current_seat(s) == 3
    assert kinds(s) == {"draw"}
    s = draw(s)
    assert len(s.hands[3]) == 12
    assert s.pending_draw == 0
    assert GAME.current_seat(s) == 0
    assert s.phase == "play"


def test_cannot_stack_lower_and_nothing_else_is_legal() -> None:
    s = make(
        cards("blue draw4", "blue 1", "blue 2"),
        cards("blue draw2", "blue 5", "wild_roulette"),
        None,
    )
    s = play(s, 0)
    assert kinds(s) == {"draw"}
    with pytest.raises(IllegalMoveError, match="stack a draw card worth 4"):
        play(s, 0)
    with pytest.raises(IllegalMoveError, match="stack"):
        play(s, 1)
    with pytest.raises(IllegalMoveError, match="stack"):
        play(s, 2, declared_colour=Colour.RED)
    with pytest.raises(IllegalMoveError, match="pass"):
        GAME.apply(s, Pass(seat=1))


def test_stack_draw_ten_on_anything() -> None:
    s = make(cards("wild_draw10", "red 1"), cards("wild_draw10", "red 1"), None)
    s = play(s, 0, declared_colour=Colour.RED)
    s = play(s, 0, declared_colour=Colour.BLUE)
    assert s.pending_draw == 20
    assert s.uno_pending == 1


def test_wild_reverse_draw_four_sends_stack_back() -> None:
    s = make(cards("blue draw4", "blue 1"), cards("wild_reverse_draw4", "red 1"), None, None)
    s = play(s, 0)
    s = play(s, 0, declared_colour=Colour.GREEN)
    assert s.direction == -1
    assert s.pending_draw == 8
    assert GAME.current_seat(s) == 0
    assert s.active_colour == Colour.GREEN


def test_wild_reverse_draw_four_normal_and_two_player() -> None:
    s = make(cards("wild_reverse_draw4", "blue 1"), None, None)
    s2 = play(s, 0, declared_colour=Colour.RED)
    assert GAME.current_seat(s2) == 2
    assert s2.pending_draw == 4
    two = make(cards("wild_reverse_draw4", "wild_draw6", "blue 1"), None)
    t = play(two, 0, declared_colour=Colour.RED)
    assert GAME.current_seat(t) == 0
    assert t.pending_draw == 4
    t2 = play(t, 0, declared_colour=Colour.RED)
    assert GAME.current_seat(t2) == 1
    assert t2.pending_draw == 10
    t3 = draw(t)
    assert len(t3.hands[0]) == 6
    assert GAME.current_seat(t3) == 1


def test_colour_roulette_draws_until_colour_and_skips_victim() -> None:
    s = make(
        cards("wild_roulette", "blue 1"),
        None,
        None,
        draw_pile=cards("green 1", "wild_draw6", "red 2", "yellow 5"),
    )
    s2 = play(s, 0, declared_colour=Colour.RED)
    assert s2.hands[1] == cards(*FILLER, "green 1", "wild_draw6", "red 2")
    assert GAME.current_seat(s2) == 2
    assert s2.active_colour == Colour.RED
    assert s2.pending_draw == 0


def test_colour_roulette_capped_at_available_cards() -> None:
    s = make(cards("wild_roulette", "blue 1"), None, draw_pile=cards("green 1", "green 2"))
    s2 = play(s, 0, declared_colour=Colour.RED)
    # the pile runs dry, the old discard (blue 1) is reshuffled in and drawn too, then it stops
    assert s2.hands[1] == cards(*FILLER, "green 1", "green 2", "blue 1")
    assert s2.draw_pile == ()
    assert s2.discard_pile == cards("wild_roulette")


# -- mercy rule ---------------------------------------------------------------


def test_mercy_elimination_mid_game_and_turn_skipping() -> None:
    big = cards(*["red 1"] * 20)
    s = make(cards("wild_draw6", "blue 1"), big, None, None, draw_pile=cards(*["yellow 1"] * 10))
    s = play(s, 0, declared_colour=Colour.RED)
    s = draw(s)
    assert s.eliminated == (1,)
    assert s.hands[1] == ()
    assert s.draw_pile[-26:-6] == big  # 20 red 1s then the 6 drawn yellows, under the pile
    assert s.scores[1] == 250
    assert GAME.current_seat(s) == 2
    assert not GAME.is_terminal(s)
    # seat 1 is skipped from now on
    s = play(s.model_copy(update={"active_colour": Colour.GREEN}), 0)
    assert GAME.current_seat(s) == 3
    s = play(s, 0)
    assert GAME.current_seat(s) == 0
    assert 1 not in {m.target_seat for m in GAME.legal_moves(s, 0) if isinstance(m, Play)}


def test_last_seat_standing_wins() -> None:
    big = cards(*["red 1"] * 23)
    s = make(cards("blue draw2", "blue 1"), big, draw_pile=cards("yellow 1", "yellow 2"))
    s = draw(play(s, 0))
    assert s.eliminated == (1,)
    assert s.winner == 0
    assert GAME.is_terminal(s)
    assert GAME.ranking(s) == [0, 1]


def test_single_draw_can_eliminate() -> None:
    s = make(cards(*["red 1"] * 24), None, draw_pile=cards("yellow 1", "yellow 2"))
    s2 = draw(s)
    assert s2.eliminated == (0,)
    assert s2.winner == 1


# -- UNO ------------------------------------------------------------------------


def test_uno_window_and_challenge() -> None:
    s = make(cards("blue 5", "red 1"), None, None)
    with pytest.raises(IllegalMoveError, match="called_uno"):
        play(make(cards("blue 5", "red 1", "red 2"), None), 0, called_uno=True)
    called = play(s, 0, called_uno=True)
    assert called.uno_pending is None
    forgot = play(s, 0)
    assert forgot.uno_pending == 0
    assert GAME.player_view(forgot, 1).table["uno_pending"] == 0
    legal = GAME.legal_moves(forgot, 1)
    assert ChallengeUno(seat=1, target=0) in legal
    assert not any(isinstance(m, ChallengeUno) for m in GAME.legal_moves(forgot, 2))
    with pytest.raises(IllegalMoveError, match="not your turn"):
        GAME.apply(forgot, ChallengeUno(seat=2, target=0))
    with pytest.raises(IllegalMoveError, match="challenge"):
        GAME.apply(forgot, ChallengeUno(seat=1, target=2))
    caught = GAME.apply(forgot, ChallengeUno(seat=1, target=0))
    assert len(caught.hands[0]) == 3
    assert caught.uno_pending is None
    assert GAME.current_seat(caught) == 1
    assert caught.move_count == forgot.move_count + 1
    # window closes once the next seat acts
    acted = draw(forgot)
    assert acted.uno_pending is None
    assert not any(isinstance(m, ChallengeUno) for m in GAME.legal_moves(acted, 1))


def test_uno_challenge_applies_mercy() -> None:
    # seat 0 plays a 0 down to one card without calling; the rotation hands it 23 cards
    s = make(cards("blue 0", "blue 1"), None, cards(*["red 1"] * 23))
    s = play(s, 0)
    assert s.uno_pending == 0
    assert len(s.hands[0]) == 23
    s = GAME.apply(s, ChallengeUno(seat=1, target=0))
    assert s.eliminated == (0,)
    assert s.winner is None
    assert GAME.current_seat(s) == 1


# -- draw pile, reshuffle, no progress -----------------------------------------


def test_reshuffle_when_draw_pile_empty() -> None:
    discard = cards("red 1", "red 2", "red 3", "blue 1")
    s = make(cards("green 9"), None, draw_pile=()).model_copy(update={"discard_pile": discard})
    s2 = draw(s)
    assert s2.discard_pile == cards("blue 1")
    assert len(s2.draw_pile) == 2
    assert len(s2.hands[0]) == 2
    assert sorted(c.rank for c in (*s2.draw_pile, s2.hands[0][-1])) == ["1", "2", "3"]
    # deterministic: same state, same shuffle
    assert draw(s) == s2


def test_penalty_draw_reshuffles_and_caps() -> None:
    s = make(cards("blue draw4", "blue 1"), None, draw_pile=cards("red 1")).model_copy(
        update={"discard_pile": cards("red 2", "blue 1")}
    )
    s2 = draw(play(s, 0))
    assert len(s2.hands[1]) == 6  # 3 filler + red 1 + reshuffled red 2, blue 1; nothing more
    assert s2.draw_pile == ()
    assert s2.discard_pile == cards("blue draw4")


def test_no_progress_terminates_by_ranking() -> None:
    s = make(cards("red 5"), cards("green 2", "green 3"), draw_pile=())
    assert GAME.legal_moves(s, 0) == []  # this state would already be flagged by apply
    s = make(cards("blue 4", "green 2"), cards("blue 5", "green 3"), top="red 1", turn=1)
    s = s.model_copy(update={"draw_pile": cards("yellow 7")})
    s = draw(s)  # seat 1 takes the last card, cannot play it
    s2 = GAME.apply(s, Pass(seat=1))  # seat 0 has no play and nothing to draw
    assert s2.stalled
    assert GAME.is_terminal(s2)
    assert GAME.current_seat(s2) is None
    assert GAME.ranking(s2) == [0, 1]  # 6 points vs 15
    with pytest.raises(IllegalMoveError):
        GAME.apply(s2, Draw(seat=0))


# -- scoring, ranking, views ----------------------------------------------------


def test_scores_and_ranking_with_eliminated_seats() -> None:
    s = make(
        cards("red 9", "red skip"),
        cards("wild_draw6"),
        cards("blue 2"),
        cards("red 9"),
        top="red 1",
        eliminated=(3, 1),
    )
    s = play(s, 0)  # play red 9, leaves a 20-point Skip
    assert s.scores == (20, 250, 2, 250)
    assert GAME.ranking(s) == [2, 0, 1, 3]
    assert s.uno_pending == 0
    w = play(s.model_copy(update={"turn": 2, "active_colour": Colour.BLUE}), 0)
    assert w.winner == 2
    assert GAME.ranking(w) == [2, 0, 1, 3]


def test_player_view_table_and_phase() -> None:
    s = make(cards("blue draw2", "blue 1"), None, None)
    v = GAME.player_view(s, 0)
    assert v.phase == "play"
    assert v.table["discard_top"] == {"colour": "blue", "rank": "1"}
    assert v.table["active_colour"] == "blue"
    assert v.table["draw_pile_count"] == 4
    assert v.table["discard_pile_count"] == 1
    assert v.table["pending_draw"] == 0
    assert v.hand_counts == [2, 3, 3]
    s2 = play(s, 0)
    assert GAME.player_view(s2, 1).phase == "draw_response"
    assert GAME.player_view(s2, 1).table["pending_draw"] == 2
    assert GAME.player_view(s2, 2).legal_moves == []


def test_describe_and_parse_moves() -> None:
    assert (
        GAME.describe_move(Play(seat=2, card_index=0, target_seat=0, card=card("green 7")))
        == "seat 2 plays green 7 and swaps hands with seat 0"
    )
    assert GAME.describe_move(Draw(seat=1, count=12)) == "seat 1 draws 12 cards"
    assert GAME.describe_move(Draw(seat=1, count=1)) == "seat 1 draws a card"
    assert (
        GAME.describe_move(
            Play(seat=3, card_index=1, declared_colour=Colour.RED, card=card("wild_draw10"))
        )
        == "seat 3 plays Wild Draw Ten (chooses red)"
    )
    assert GAME.describe_move(Play(seat=0, card_index=4, called_uno=True)) == (
        "seat 0 plays card 4 and calls UNO"
    )
    assert GAME.describe_move(Pass(seat=0)) == "seat 0 passes"
    assert "UNO" in GAME.describe_move(ChallengeUno(seat=0, target=1))
    assert GAME.parse_move({"kind": "draw", "seat": 1}) == Draw(seat=1)
    assert GAME.parse_move(
        {"kind": "play", "seat": 1, "card_index": 2, "declared_colour": "red"}
    ) == Play(seat=1, card_index=2, declared_colour=Colour.RED)
    with pytest.raises(ValueError, match="kind"):
        GAME.parse_move({"kind": "steal", "seat": 1})
    s = GAME.new_game(1, 3)
    for m in GAME.legal_moves(s, 0):
        assert GAME.apply(s, m).move_count == 1
        if isinstance(m, Draw):
            assert m.count == 1
    assert GAME.rules_markdown().startswith("# UNO Show 'em No Mercy")
