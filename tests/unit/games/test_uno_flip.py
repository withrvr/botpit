"""UNO Flip!: the contract suite (privacy check replaced by a Flip-specific one,
since hands hold double-faced cards but views show only the active face) plus
scripted scenarios on hand-made states."""

from __future__ import annotations

import json
import random
from collections import Counter
from typing import Any

import pytest
from pydantic import ValidationError

from botpit.games import GAME_CATALOG
from botpit.games.base import IllegalMoveError
from botpit.games.cards import Colour, UnoCard, rng_for_seed
from botpit.games.uno_flip import GAME
from botpit.games.uno_flip.cards import FlipCard, build_deck, dark_faces, light_faces, points
from botpit.games.uno_flip.game import HAND_SIZE, hand_points
from botpit.games.uno_flip.moves import (
    AcceptWildDraw,
    ChallengeUno,
    ChallengeWildDraw,
    Draw,
    FlipMove,
    Pass,
    Play,
)
from botpit.games.uno_flip.state import FlipState
from tests.unit.games import contract as c

SEATS = list(range(GAME.min_seats, GAME.max_seats + 1))
CHECKS = [f for f in c.ALL_CHECKS if f is not c.check_player_view_privacy]


@pytest.mark.parametrize("seats", SEATS)
@pytest.mark.parametrize("check", CHECKS, ids=lambda f: f.__name__)
def test_contract(check: Any, seats: int) -> None:
    check(GAME, seats)


def test_registered() -> None:
    assert GAME_CATALOG["uno_flip"] is GAME
    assert GAME.title == "UNO Flip!"


# --- privacy -----------------------------------------------------------------


def _scramble_hidden_faces(state: FlipState) -> FlipState:
    """Replace every hidden face with a different one; the view must not notice."""
    hidden = "dark" if state.side == "light" else "light"
    swap = {
        "light": UnoCard(colour=Colour.RED, rank="9"),
        "dark": UnoCard(colour=None, rank="wild"),
    }

    def sub(card: FlipCard) -> FlipCard:
        return card.model_copy(update={hidden: swap[hidden]})

    return state.model_copy(
        update={
            "hands": tuple(tuple(sub(cd) for cd in h) for h in state.hands),
            "draw_pile": tuple(sub(cd) for cd in state.draw_pile),
            "discard": tuple(sub(cd) for cd in state.discard),
        }
    )


def _no_face_pairs(obj: Any) -> None:
    if isinstance(obj, dict):
        assert not {"light", "dark"} & obj.keys()
        for v in obj.values():
            _no_face_pairs(v)
    elif isinstance(obj, list):
        for v in obj:
            _no_face_pairs(v)


@pytest.mark.parametrize("seats", [2, 5, 10])
def test_player_view_privacy(seats: int) -> None:
    rng = random.Random(0)
    trace = c.random_playout(GAME, seats, seed=9)
    for state, _ in trace[::5]:
        assert isinstance(state, FlipState)
        for seat in range(seats):
            view = GAME.player_view(state, seat)
            assert view.seat == seat
            assert view.hand_counts == [len(h) for h in state.hands]
            own = [
                cd.face(state.side).model_dump(mode="json") | {"index": i}
                for i, cd in enumerate(state.hands[seat])
            ]
            assert view.hand == own
            _no_face_pairs(json.loads(view.model_dump_json()))
            assert view.terminal == GAME.is_terminal(state)
            assert view.current_seat == GAME.current_seat(state)
            expected = [m.model_dump(mode="json") for m in GAME.legal_moves(state, seat)]
            assert view.legal_moves == expected
            if seat != view.current_seat:
                assert view.legal_moves == []
            if "drawn_card" in view.table:
                assert seat == state.turn
                assert state.phase == "after_draw"
            shuffled = c._permute_other_hands(state, seat, rng)
            assert GAME.player_view(shuffled, seat) == view
            assert GAME.player_view(_scramble_hidden_faces(state), seat) == view


# --- deck ----------------------------------------------------------------------


def test_deck_composition() -> None:
    deck = build_deck(rng_for_seed(1))
    assert len(deck) == 112
    light = Counter((f.colour, f.rank) for f in light_faces())
    dark = Counter((f.colour, f.rank) for f in dark_faces())
    assert Counter((cd.light.colour, cd.light.rank) for cd in deck) == light
    assert Counter((cd.dark.colour, cd.dark.rank) for cd in deck) == dark
    for colour in (Colour.RED, Colour.YELLOW, Colour.GREEN, Colour.BLUE):
        assert light[colour, "0"] == 0
        for rank in [*map(str, range(1, 10)), "draw1", "reverse", "skip", "flip"]:
            assert light[colour, rank] == 2
    for colour in (Colour.PINK, Colour.TEAL, Colour.ORANGE, Colour.PURPLE):
        for rank in [*map(str, range(1, 10)), "draw5", "reverse", "skip_all", "flip"]:
            assert dark[colour, rank] == 2
    assert light[None, "wild"] == light[None, "wild_draw2"] == 4
    assert dark[None, "wild"] == dark[None, "wild_draw_colour"] == 4
    assert build_deck(rng_for_seed(1)) == deck
    assert build_deck(rng_for_seed(2)) != deck


def test_points_table() -> None:
    table = {
        "7": 7,
        "draw1": 10,
        "draw5": 20,
        "reverse": 20,
        "skip": 20,
        "flip": 20,
        "skip_all": 30,
        "wild": 40,
        "wild_draw2": 50,
        "wild_draw_colour": 60,
    }
    for rank, value in table.items():
        assert points(UnoCard(colour=None, rank=rank)) == value


# --- hand-made states ---------------------------------------------------------

L = {"r": Colour.RED, "y": Colour.YELLOW, "g": Colour.GREEN, "b": Colour.BLUE}
D = {"p": Colour.PINK, "t": Colour.TEAL, "o": Colour.ORANGE, "u": Colour.PURPLE}


def face(spec: str) -> UnoCard:
    """'r7' -> red 7, 'tskip_all' -> teal skip_all, 'wild' -> wild."""
    if spec[0] in L and spec[1:]:
        return UnoCard(colour=L[spec[0]], rank=spec[1:])
    if spec[0] in D and spec[1:]:
        return UnoCard(colour=D[spec[0]], rank=spec[1:])
    return UnoCard(colour=None, rank=spec)


def card(light: str, dark: str = "p1") -> FlipCard:
    return FlipCard(light=face(light), dark=face(dark))


def cards(*specs: str) -> tuple[FlipCard, ...]:
    return tuple(card(*s.split("/")) for s in specs)


def make(
    hands: list[list[str]],
    discard: list[str],
    draw: list[str] | None = None,
    *,
    side: str = "light",
    turn: int = 0,
    colour: str | None = "auto",
    **extra: Any,
) -> FlipState:
    """Discard/draw lists are bottom-to-top. 'r7/t3' gives both faces; a single
    spec sets the light face (dark face defaults to pink 1) or the dark face if
    side='dark' (light defaults to red 1)."""

    def one(spec: str) -> FlipCard:
        if "/" in spec:
            return card(*spec.split("/"))
        return card(spec) if side == "light" else card("r1", spec)

    st = FlipState(
        slug="uno_flip",
        seed=0,
        seat_count=len(hands),
        hands=tuple(tuple(one(s) for s in h) for h in hands),
        scores=(0,) * len(hands),
        draw_pile=tuple(one(s) for s in (["r1/p1"] * 6 if draw is None else draw)),
        discard=tuple(one(s) for s in discard),
        side=side,  # type: ignore[arg-type]
        turn=turn,
        direction=1,
        active_colour=None,
        **extra,
    )
    if colour == "auto":
        return st.model_copy(update={"active_colour": st.discard[-1].face(st.side).colour})
    return st.model_copy(update={"active_colour": None if colour is None else Colour(colour)})


def play(seat: int, index: int, colour: str | None = None, *, uno: bool = False) -> Play:
    return Play(
        seat=seat,
        card_index=index,
        declared_colour=None if colour is None else Colour(colour),
        called_uno=uno,
    )


def kinds(state: FlipState) -> set[str]:
    return {m.kind for m in GAME.legal_moves(state, state.turn)}


def sizes(state: FlipState) -> list[int]:
    return [len(h) for h in state.hands]


# --- first discard -------------------------------------------------------------


def _first_light_rank(seed: int, seats: int) -> str:
    deck = build_deck(rng_for_seed(seed))
    return deck[seats * HAND_SIZE :][-1].light.rank


def _seed_where(seats: int, rank: str) -> int:
    for seed in range(2000):
        if _first_light_rank(seed, seats) == rank:
            return seed
    raise AssertionError(rank)


def test_first_discard_number_and_deal() -> None:
    seed = _seed_where(4, "5")
    st = GAME.new_game(seed, 4)
    assert st.side == "light"
    assert st.turn == 0
    assert st.direction == 1
    assert sizes(st) == [7] * 4
    assert st.active_colour == st.discard[-1].light.colour
    assert len(st.draw_pile) + len(st.discard) + sum(sizes(st)) == 112
    assert st.phase == "play"
    assert st.move_count == 0


def test_first_discard_draw1() -> None:
    st = GAME.new_game(_seed_where(3, "draw1"), 3)
    assert sizes(st) == [8, 7, 7]
    assert st.turn == 1


def test_first_discard_skip() -> None:
    st = GAME.new_game(_seed_where(3, "skip"), 3)
    assert st.turn == 1
    assert sizes(st) == [7, 7, 7]


def test_first_discard_reverse() -> None:
    st = GAME.new_game(_seed_where(4, "reverse"), 4)
    assert st.direction == -1
    assert st.turn == 3
    st2 = GAME.new_game(_seed_where(2, "reverse"), 2)
    assert st2.direction == -1
    assert st2.turn == 1


def test_first_discard_flip() -> None:
    st = GAME.new_game(_seed_where(3, "flip"), 3)
    assert st.side == "dark"
    assert st.turn == 0
    assert st.discard[-1].light.rank == "flip"
    assert st.active_colour == st.discard[-1].dark.colour
    assert sizes(st) == [7, 7, 7]


def test_first_discard_wild() -> None:
    st = GAME.new_game(_seed_where(3, "wild"), 3)
    assert st.active_colour is None
    assert st.turn == 0
    assert all(m.kind == "play" for m in GAME.legal_moves(st, 0))
    assert len([m for m in GAME.legal_moves(st, 0) if m.kind == "play"]) >= 7


def test_first_discard_wild_draw2_reshuffled() -> None:
    seed = _seed_where(3, "wild_draw2")
    st = GAME.new_game(seed, 3)
    assert st.discard[-1].light.rank != "wild_draw2"
    assert len(st.discard) == 1
    assert Counter(build_deck(rng_for_seed(seed))) == Counter(
        [*st.draw_pile, *st.discard, *(cd for h in st.hands for cd in h)]
    )
    assert GAME.new_game(seed, 3) == st


# --- flip mechanics ------------------------------------------------------------


def test_flip_turns_discard_over() -> None:
    st = make(
        [["rflip/t9", "r3/o4"], ["b2/u7", "g1/p5"]],
        discard=["y4/pskip_all", "r9/o2", "r5/wild_draw_colour"],
        draw=["b8/t1", "g7/u3"],
    )
    nxt = GAME.apply(st, play(0, 0))
    assert nxt.side == "dark"
    assert [cd.light.rank for cd in nxt.discard] == ["flip", "5", "9", "4"]
    assert nxt.discard[-1].dark == face("pskip_all")
    assert nxt.active_colour == Colour.PINK  # skip_all does not trigger
    assert nxt.turn == 1
    assert nxt.hands[0] == st.hands[0][1:]
    assert nxt.hands[1] == st.hands[1]
    assert nxt.draw_pile == st.draw_pile
    view = GAME.player_view(nxt, 1)
    assert view.table["side"] == "dark"
    assert view.hand == [
        {"colour": "purple", "rank": "7", "index": 0},
        {"colour": "pink", "rank": "5", "index": 1},
    ]
    # seat 1 must match the new top: purple 7 does not, pink 5 does.
    assert {m.card_index for m in GAME.legal_moves(nxt, 1) if isinstance(m, Play)} == {1}


def test_flip_onto_wild_face_opens_colour_and_flips_back() -> None:
    st = make(
        [["rflip/t9", "r3/o4"], ["b2/u7", "g1/p5"]],
        discard=["r4/wild", "r5/o2"],
    )
    nxt = GAME.apply(st, play(0, 0))
    assert nxt.side == "dark"
    assert nxt.active_colour is None
    assert {m.card_index for m in GAME.legal_moves(nxt, 1) if isinstance(m, Play)} == {0, 1}
    back = make(
        [["r3/t9"], ["g1/pflip", "g1/p5"]],
        discard=["r4/wild", "r5/o2", "r7/p3"],
        side="dark",
        turn=1,
    )
    flipped = GAME.apply(back, play(1, 0))
    assert flipped.side == "light"
    assert flipped.active_colour == Colour.RED
    assert flipped.discard[-1].light == face("r4")


# --- light actions -------------------------------------------------------------


def test_light_draw1_skip_reverse() -> None:
    st = make([["rdraw1", "r3"], ["b2"], ["g1"]], discard=["r9"])
    nxt = GAME.apply(st, play(0, 0))
    assert sizes(nxt) == [1, 2, 1]
    assert nxt.turn == 2
    st = make([["rskip", "r3"], ["b2"], ["g1"]], discard=["r9"])
    assert GAME.apply(st, play(0, 0)).turn == 2
    st = make([["rreverse", "r3"], ["b2"], ["g1"]], discard=["r9"])
    nxt = GAME.apply(st, play(0, 0))
    assert nxt.direction == -1
    assert nxt.turn == 2
    st = make([["rreverse", "r3"], ["b2"]], discard=["r9"])
    nxt = GAME.apply(st, play(0, 0))
    assert nxt.direction == -1
    assert nxt.turn == 0


def test_wild_declares_side_colour() -> None:
    st = make([["wild", "r3"], ["b2"]], discard=["g9"])
    nxt = GAME.apply(st, play(0, 0, "blue"))
    assert nxt.active_colour == Colour.BLUE
    assert nxt.turn == 1
    with pytest.raises(IllegalMoveError, match="not a light-side colour"):
        GAME.apply(st, play(0, 0, "teal"))
    with pytest.raises(IllegalMoveError, match="declare a colour"):
        GAME.apply(st, play(0, 0))
    with pytest.raises(IllegalMoveError, match="only wild"):
        GAME.apply(make([["g3", "r3"], ["b2"]], discard=["g9"]), play(0, 0, "red"))


def test_wild_draw2_accept_and_challenges() -> None:
    # seat 0 holds a green card while green is active: the play is a bluff.
    bluff = make([["wild_draw2", "g3"], ["b2", "b4"], ["y1"]], discard=["g9"])
    pend = GAME.apply(bluff, play(0, 0, "blue", uno=True))
    assert pend.phase == "wild_draw_response"
    assert pend.turn == 1
    assert pend.pending_from == 0
    assert pend.pending_prev_colour == Colour.GREEN
    assert kinds(pend) == {"accept_wild_draw", "challenge_wild_draw"}
    assert GAME.player_view(pend, 1).table["pending_draw"] == 2
    with pytest.raises(IllegalMoveError, match="respond to the wild draw"):
        GAME.apply(pend, play(1, 0))

    accepted = GAME.apply(pend, AcceptWildDraw(seat=1))
    assert sizes(accepted) == [1, 4, 1]
    assert accepted.turn == 2
    assert accepted.phase == "play"
    assert accepted.active_colour == Colour.BLUE

    caught = GAME.apply(pend, ChallengeWildDraw(seat=1))
    assert sizes(caught) == [3, 2, 1]
    assert caught.turn == 1
    assert caught.phase == "play"
    assert caught.pending_from is None

    honest = make([["wild_draw2", "r3"], ["b2", "b4"], ["y1"]], discard=["g9"])
    pend = GAME.apply(honest, play(0, 0, "blue", uno=True))
    failed = GAME.apply(pend, ChallengeWildDraw(seat=1))
    assert sizes(failed) == [1, 6, 1]
    assert failed.turn == 2


# --- dark actions --------------------------------------------------------------


def test_dark_draw5_and_skip_all() -> None:
    st = make([["pdraw5", "p3"], ["t2"], ["o1"]], discard=["p9"], side="dark")
    nxt = GAME.apply(st, play(0, 0))
    assert sizes(nxt) == [1, 6, 1]
    assert nxt.turn == 2
    st = make([["pskip_all", "p3"], ["t2"], ["o1"]], discard=["p9"], side="dark")
    nxt = GAME.apply(st, play(0, 0))
    assert nxt.turn == 0
    assert nxt.direction == 1
    two = make([["pskip_all", "p3"], ["t2"]], discard=["p9"], side="dark")
    assert GAME.apply(two, play(0, 0)).turn == 0


def test_draw5_caps_at_available_cards() -> None:
    st = make([["pdraw5", "p3"], ["t2"]], discard=["u4", "p9"], draw=["t1"], side="dark")
    nxt = GAME.apply(st, play(0, 0))
    assert sizes(nxt) == [1, 4]  # 1 from the pile + 2 reshuffled from under the Draw Five
    assert len(nxt.draw_pile) == 0
    assert len(nxt.discard) == 1


def test_wild_draw_colour_accept_and_challenges() -> None:
    draw = ["t1", "o2", "p3", "u4", "p5"]  # top is p5 -> last drawn first
    bluff = make([["wild_draw_colour", "p3"], ["t2", "t4"], ["o1"]], ["p9"], draw, side="dark")
    pend = GAME.apply(bluff, play(0, 0, "orange"))
    assert pend.phase == "wild_draw_response"
    assert pend.pending_prev_colour == Colour.PINK
    assert GAME.player_view(pend, 1).table["pending_draw"] == "colour:orange"

    accepted = GAME.apply(pend, AcceptWildDraw(seat=1))
    assert [cd.dark.rank for cd in accepted.hands[1]] == ["2", "4", "5", "4", "3", "2"]
    assert accepted.turn == 2
    assert accepted.active_colour == Colour.ORANGE

    caught = GAME.apply(pend, ChallengeWildDraw(seat=1))
    assert sizes(caught) == [5, 2, 1]
    assert caught.turn == 1

    honest = make([["wild_draw_colour", "t3"], ["t2", "t4"], ["o1"]], ["p9"], draw, side="dark")
    pend = GAME.apply(honest, play(0, 0, "orange"))
    failed = GAME.apply(pend, ChallengeWildDraw(seat=1))
    assert sizes(failed) == [1, 8, 1]
    assert failed.turn == 2


def test_wild_draw_colour_never_shows_stops_at_cap() -> None:
    st = make([["wild_draw_colour", "p3"], ["t2"]], ["u4", "p9"], ["t1", "t5"], side="dark")
    pend = GAME.apply(st, play(0, 0, "orange"))
    done = GAME.apply(pend, AcceptWildDraw(seat=1))
    assert sizes(done) == [1, 5]  # 2 from the pile + 2 reshuffled, orange never shows
    assert len(done.draw_pile) == 0
    assert len(done.discard) == 1


def test_wild_draw_as_last_card_ends_game_with_penalty() -> None:
    st = make([["wild_draw2"], ["b2", "b4"]], ["g9"])
    end = GAME.apply(st, play(0, 0, "blue"))
    assert end.terminal
    assert end.winner == 0
    assert sizes(end) == [0, 4]
    assert end.scores == (0, 8)


# --- draw then play ------------------------------------------------------------


def test_draw_then_play_or_pass() -> None:
    st = make([["r3", "b5"], ["g1"]], ["g9"], ["y9", "b2", "g4"])  # draws g4
    assert kinds(st) == {"draw"}
    drawn = GAME.apply(st, Draw(seat=0))
    assert drawn.phase == "after_draw"
    assert sizes(drawn) == [3, 1]
    assert GAME.player_view(drawn, 0).table["drawn_card"] == {"colour": "green", "rank": "4"}
    assert "drawn_card" not in GAME.player_view(drawn, 1).table
    assert kinds(drawn) == {"play", "pass"}
    with pytest.raises(IllegalMoveError, match="only play the drawn card"):
        GAME.apply(drawn, play(0, 0))
    with pytest.raises(IllegalMoveError, match="phase after_draw"):
        GAME.apply(drawn, Draw(seat=0))
    played = GAME.apply(drawn, play(0, 2))
    assert played.turn == 1
    assert played.phase == "play"
    assert sizes(played) == [2, 1]
    passed = GAME.apply(drawn, Pass(seat=0))
    assert passed.turn == 1
    assert passed.phase == "play"
    assert sizes(passed) == [3, 1]
    with pytest.raises(IllegalMoveError, match="only pass after"):
        GAME.apply(st, Pass(seat=0))


def test_drawn_card_not_matching_forces_pass() -> None:
    st = make([["r3"], ["g1"]], ["g9"], ["y9", "b2"])
    drawn = GAME.apply(st, Draw(seat=0))
    assert kinds(drawn) == {"pass"}


def test_cannot_draw_with_playable_card() -> None:
    st = make([["g3", "b5"], ["g1"]], ["g9"])
    assert kinds(st) == {"play"}
    with pytest.raises(IllegalMoveError, match="must play"):
        GAME.apply(st, Draw(seat=0))


def test_reshuffle_when_draw_pile_empty() -> None:
    st = make([["r3"], ["g1"]], ["b1", "b2", "b3", "g9"], draw=[])
    drawn = GAME.apply(st, Draw(seat=0))
    assert len(drawn.discard) == 1
    assert drawn.discard[0].light == face("g9")
    assert len(drawn.draw_pile) == 2
    assert sizes(drawn) == [2, 1]
    assert GAME.apply(st, Draw(seat=0)) == drawn  # deterministic reshuffle


# --- UNO -----------------------------------------------------------------------


def test_uno_window() -> None:
    st = make([["g3", "b5"], ["g1", "g2", "g4"], ["y7"]], ["g9"])
    moves = GAME.legal_moves(st, 0)
    assert {(m.card_index, m.called_uno) for m in moves if isinstance(m, Play)} == {
        (0, False),
        (0, True),
    }
    missed = GAME.apply(st, play(0, 0))
    assert missed.uno_pending == 0
    assert missed.turn == 1
    assert ChallengeUno(seat=1, target=0) in GAME.legal_moves(missed, 1)
    caught = GAME.apply(missed, ChallengeUno(seat=1, target=0))
    assert sizes(caught) == [3, 3, 1]
    assert caught.turn == 1
    assert caught.uno_pending is None
    assert caught.move_count == missed.move_count + 1
    assert "challenge_uno" not in kinds(caught)
    # Acting closes the window for good.
    acted = GAME.apply(missed, play(1, 0))
    assert acted.uno_pending is None
    assert "challenge_uno" not in kinds(acted)
    with pytest.raises(IllegalMoveError, match="has not missed"):
        GAME.apply(acted, ChallengeUno(seat=2, target=0))
    called = GAME.apply(st, play(0, 0, uno=True))
    assert called.uno_pending is None
    with pytest.raises(IllegalMoveError, match="second-to-last"):
        GAME.apply(make([["g3", "b5", "r1"], ["g1"]], ["g9"]), play(0, 0, uno=True))
    with pytest.raises(IllegalMoveError, match="has not missed"):
        GAME.apply(missed, ChallengeUno(seat=1, target=2))


# --- end of game ---------------------------------------------------------------


def test_win_and_scores_by_side() -> None:
    light = make([["g3"], ["gskip/pskip_all", "wild_draw2/t7"], ["r5/wild"]], ["g9"])
    end = GAME.apply(light, play(0, 0))
    assert end.terminal
    assert end.winner == 0
    assert GAME.current_seat(end) is None
    assert end.scores == (0, 70, 5)
    assert GAME.ranking(end) == [0, 2, 1]
    dark = make([["p3"], ["gskip/pskip_all", "wild_draw2/t7"], ["r5/wild"]], ["p9"], side="dark")
    end = GAME.apply(dark, play(0, 0))
    assert end.scores == (0, 37, 40)
    assert GAME.ranking(end) == [0, 1, 2]
    assert GAME.legal_moves(end, 1) == []
    with pytest.raises(IllegalMoveError, match="over"):
        GAME.apply(end, play(1, 0))


def test_no_progress_terminates() -> None:
    st = make([["r1", "r5"], ["b2", "b4"], ["y6"]], ["g9"], draw=[], phase="after_draw")
    end = GAME.apply(st, Pass(seat=0))  # seat 1 cannot match green 9 and cannot draw
    assert end.terminal
    assert end.winner is None
    assert end.scores == (6, 6, 6)
    assert GAME.ranking(end) == [0, 1, 2]
    assert hand_points(end.hands[1], "light") == 6


def test_scores_hidden_until_terminal() -> None:
    st = GAME.new_game(3, 4)
    assert st.scores == (0, 0, 0, 0)
    assert sorted(GAME.ranking(st)) == [0, 1, 2, 3]


# --- moves ---------------------------------------------------------------------


def test_parse_and_describe() -> None:
    mv = GAME.parse_move({"kind": "play", "seat": 2, "card_index": 1, "declared_colour": "teal"})
    assert isinstance(mv, Play)
    assert mv.declared_colour == Colour.TEAL
    assert GAME.describe_move(mv) == "seat 2 plays card 1, declares teal"
    listed = GAME.legal_moves(make([["gflip/t7", "p2"], ["o1"]], ["t9"], side="dark"), 0)
    descs = [GAME.describe_move(m) for m in listed]
    assert descs[0] == "seat 0 plays teal 7"
    assert GAME.describe_move(Draw(seat=1)) == "seat 1 draws a card"
    flip = play(0, 0).model_copy(update={"card": face("gflip")})
    assert "flips" in GAME.describe_move(flip)
    for bad in (
        {"kind": "play", "seat": 0},
        {"kind": "explode", "seat": 0},
        {"kind": "play", "seat": 0, "card_index": 0, "extra": 1},
        {"kind": "challenge_uno", "seat": 0},
    ):
        with pytest.raises(ValidationError):
            GAME.parse_move(bad)


def test_play_card_self_check() -> None:
    st = make([["g3", "b5"], ["g1"]], ["g9"])
    ok: FlipMove = play(0, 0).model_copy(update={"card": face("g3")})
    assert GAME.apply(st, ok).turn == 1
    with pytest.raises(IllegalMoveError, match="is green 3, not blue 5"):
        GAME.apply(st, play(0, 0).model_copy(update={"card": face("b5")}))
    with pytest.raises(IllegalMoveError, match="no card at index"):
        GAME.apply(st, play(0, 5))
    with pytest.raises(IllegalMoveError, match="matches neither"):
        GAME.apply(st, play(0, 1))
