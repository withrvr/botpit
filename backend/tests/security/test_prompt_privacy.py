"""The move prompt is built from ``player_view`` only: no other seat's cards leak.

Fuzz: every game, min and max seats, 20 seeds, every 5th state, every seat.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

from app.agents import build_move_prompt, get_persona
from app.games import GAME_CATALOG, get_game
from app.games.base import AnyGame, GameState
from tests.unit.games.contract import random_playout

SLUGS = sorted(GAME_CATALOG)
SEEDS = 20
PERSONA = get_persona("quiet-shark")


def _compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"))


def _sig(card: dict[str, Any]) -> str:
    """Prefix that matches the card's JSON with or without a trailing ``index`` key."""
    return _compact({k: v for k, v in card.items() if k != "index"})[:-1]


def _cards_in(node: Any) -> list[dict[str, Any]]:
    """Card-shaped dicts (have ``rank``) nested anywhere in ``node``."""
    if isinstance(node, dict):
        if "rank" in node:
            return [node]
        return [c for v in node.values() for c in _cards_in(v)]
    if isinstance(node, list):
        return [c for v in node for c in _cards_in(v)]
    return []


def assert_no_foreign_cards(
    text: str, game: AnyGame, state: GameState, seat: int
) -> None:
    view = game.player_view(state, seat)
    own = game.hand_view(state, seat)
    assert _compact(view.hand) in text
    table_cards = _cards_in(view.table)
    assert len(table_cards) <= state.seat_count + 2  # discard/drawn/trick, never a hand
    echoes = _cards_in(
        [m.model_dump(mode="json") for m in game.legal_moves(state, seat)]
    )
    allowed = Counter(_sig(c) for c in own + table_cards + echoes)
    for other in range(state.seat_count):
        if other == seat:
            continue
        theirs = game.hand_view(state, other)
        if len(theirs) >= 3:
            assert _compact(theirs) not in text, (
                f"seat {other}'s hand in seat {seat}'s prompt"
            )
        for sig in {_sig(c) for c in theirs}:
            assert text.count(sig) <= allowed[sig], (
                f"card {sig} of seat {other} appears in seat {seat}'s prompt more than allowed"
            )


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("which", ["min", "max"])
def test_move_prompt_never_leaks_other_hands(slug: str, which: str) -> None:
    game = get_game(slug)
    seats = game.min_seats if which == "min" else game.max_seats
    checked = 0
    for seed in range(SEEDS):
        for state, _ in random_playout(game, seats, seed=1000 + seed)[::5]:
            for seat in range(seats):
                view = game.player_view(state, seat)
                bundle = build_move_prompt(game, view, PERSONA, [], None)
                assert_no_foreign_cards(bundle.user, game, state, seat)
                checked += 1
    assert checked > SEEDS * seats


@pytest.mark.parametrize("slug", SLUGS)
def test_detector_catches_a_leak(slug: str) -> None:
    game = get_game(slug)
    state = game.new_game(5, game.min_seats)
    bundle = build_move_prompt(game, game.player_view(state, 0), PERSONA, [], None)
    assert_no_foreign_cards(bundle.user, game, state, 0)
    leaky = bundle.user + "\n" + _compact(game.hand_view(state, 1))
    with pytest.raises(AssertionError):
        assert_no_foreign_cards(leaky, game, state, 0)
    one_card = bundle.user + "\n" + _compact(game.hand_view(state, 1)[0])
    with pytest.raises(AssertionError):
        assert_no_foreign_cards(one_card, game, state, 0)
