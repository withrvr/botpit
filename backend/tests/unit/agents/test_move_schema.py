"""The move schema is self-contained, closed, and accepts every legal move."""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest

from app.games import GAME_CATALOG, get_game
from app.games.move_schema import move_json_schema
from tests.unit.games.contract import random_playout

SLUGS = sorted(GAME_CATALOG)


def _walk(node: Any):  # type: ignore[no-untyped-def]
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


@pytest.mark.parametrize("slug", SLUGS)
def test_schema_is_self_contained_and_closed(slug: str) -> None:
    schema = move_json_schema(get_game(slug))
    jsonschema.Draft202012Validator.check_schema(schema)
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in schema
    for node in _walk(schema):
        # a property literally named "title" is the only place the key may survive
        assert "title" not in node or "title" in node.get("properties", {})
        if node.get("type") == "object":
            assert node["additionalProperties"] is False


@pytest.mark.parametrize("slug", SLUGS)
def test_every_legal_move_validates_and_round_trips(slug: str) -> None:
    game = get_game(slug)
    validator = jsonschema.Draft202012Validator(move_json_schema(game))
    seen = 0
    for state, _ in random_playout(game, game.min_seats, seed=21)[::4]:
        seat = game.current_seat(state)
        assert seat is not None
        for move in game.legal_moves(state, seat):
            data = move.model_dump(mode="json")
            validator.validate(data)
            assert game.parse_move(data) == move
            seen += 1
    assert seen > 10


def test_schema_rejects_unknown_keys_and_kinds() -> None:
    validator = jsonschema.Draft202012Validator(
        move_json_schema(get_game("uno_classic"))
    )
    assert not validator.is_valid({"kind": "draw", "seat": 0, "extra": 1})
    assert not validator.is_valid({"kind": "teleport", "seat": 0})
    assert validator.is_valid({"kind": "draw", "seat": 0})


def test_schema_is_cached_by_slug() -> None:
    game = get_game("hearts")
    assert move_json_schema(game) is move_json_schema(game)
