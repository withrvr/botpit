"""Prompt bundles: structure, persona, retry line, chat trimming, budget, privacy signature."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import app.agents.prompt as prompt_module
from app.agents import (
    PERSONAS,
    PROMPT_TOKEN_BUDGET,
    build_chat_prompt,
    build_move_prompt,
    estimate_tokens,
    get_persona,
)
from app.events import ChatMessage
from app.games import GAME_CATALOG, get_game
from app.games.base import PlayerView
from app.games.move_schema import move_json_schema
from tests.unit.games.contract import random_playout

SLUGS = sorted(GAME_CATALOG)
PERSONA = get_persona("gambler")


def _view(slug: str = "uno_classic", seats: int | None = None) -> PlayerView:
    game = get_game(slug)
    state = game.new_game(3, seats or game.min_seats)
    return game.player_view(state, 0)


def test_bundle_structure_and_persona_text() -> None:
    game = get_game("uno_classic")
    view = _view()
    bundle = build_move_prompt(game, view, PERSONA, [], None)
    assert bundle.system.startswith(game.rules_markdown().rstrip())
    assert "## Persona" in bundle.system
    assert PERSONA.name in bundle.system
    assert PERSONA.risk in bundle.system
    assert PERSONA.voice in bundle.system
    assert '"reasoning"' in bundle.system
    assert json.dumps(view.hand, separators=(",", ":")) in bundle.user
    assert bundle.json_schema == {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "move": move_json_schema(game),
        },
        "required": ["reasoning", "move"],
        "additionalProperties": False,
    }


def test_last_error_line_only_when_given() -> None:
    game = get_game("uno_classic")
    view = _view()
    assert "rejected" not in build_move_prompt(game, view, PERSONA, [], None).user
    user = build_move_prompt(game, view, PERSONA, [], "not your turn").user
    assert "Your previous move was rejected: not your turn." in user
    assert "Choose a different move from legal_moves." in user


def test_chat_trimmed_to_ten_most_recent() -> None:
    game = get_game("uno_classic")
    chat = [ChatMessage(seat=i % 2, text=f"line {i}") for i in range(25)]
    user = build_move_prompt(game, _view(), PERSONA, chat, None).user
    for i in range(15):
        assert f"line {i}\n" not in user
        assert not user.endswith(f"line {i}")
    for i in range(15, 25):
        assert f"seat {i % 2}: line {i}" in user


@pytest.mark.parametrize("slug", SLUGS)
def test_move_prompt_fits_budget_at_max_seats(slug: str) -> None:
    game = get_game(slug)
    chat = [ChatMessage(seat=0, text="x" * 200) for _ in range(10)]
    views = [_view(slug, game.max_seats)]
    views += [
        game.player_view(state, move.seat)
        for state, move in random_playout(game, game.max_seats, seed=4)[::10]
    ]
    for view in views:
        for persona in PERSONAS.values():
            bundle = build_move_prompt(game, view, persona, chat, "bad move")
            assert estimate_tokens(bundle.system + bundle.user) <= PROMPT_TOKEN_BUDGET


def test_budget_trims_chat_then_how_to_think(monkeypatch: pytest.MonkeyPatch) -> None:
    game = get_game("uno_flip")
    view = _view("uno_flip", 10)
    chat = [ChatMessage(seat=0, text="y" * 150) for _ in range(10)]
    full = build_move_prompt(game, view, PERSONA, chat, None)
    assert "## How to think" in full.system
    monkeypatch.setattr(prompt_module, "PROMPT_TOKEN_BUDGET", 2100)
    tight = build_move_prompt(game, view, PERSONA, chat, None)
    assert tight.user.count("seat 0: y") < 10
    assert "## How to think" not in tight.system
    assert estimate_tokens(tight.system + tight.user) <= 2100


def test_chat_prompt() -> None:
    game = get_game("hearts")
    view = _view("hearts")
    bundle = build_chat_prompt(game, view, PERSONA, "seat 0 plays 2C", [])
    assert bundle.json_schema == {
        "type": "object",
        "properties": {"say": {"type": ["string", "null"], "maxLength": 200}},
        "required": ["say"],
        "additionalProperties": False,
    }
    assert PERSONA.voice in bundle.system
    assert "null" in bundle.system
    assert "seat 0 plays 2C" in bundle.user
    assert json.dumps(view.hand, separators=(",", ":")) not in bundle.user


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100


def test_builders_only_accept_player_view() -> None:
    for fn in (build_move_prompt, build_chat_prompt):
        params = inspect.signature(fn, eval_str=True).parameters
        assert params["view"].annotation is PlayerView
        assert not any(p.annotation.__name__ == "GameState" for p in params.values())
    source = Path(prompt_module.__file__).read_text(encoding="utf-8")
    assert "GameState" not in source
    assert ".hands[" not in source
