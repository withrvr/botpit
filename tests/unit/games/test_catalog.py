"""Catalog-level guarantees: every registered game is reachable by slug, states
survive the event-log round trip (dict payload -> JSON -> model), and rules
plus move parsing work through the generic interface only."""

from __future__ import annotations

import json

import pytest

from botpit.events import PUBLIC, EventEnvelope, MoveApplied, make_event
from botpit.games import GAME_CATALOG, get_game
from tests.unit.games import contract as c

EXPECTED = {"uno_classic", "uno_flip", "uno_no_mercy", "hearts"}


def test_catalog_lists_v1_games() -> None:
    assert set(GAME_CATALOG) == EXPECTED
    for slug, game in GAME_CATALOG.items():
        assert game.slug == slug
        assert get_game(slug) is game
    with pytest.raises(KeyError, match="unknown game"):
        get_game("poker")


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_state_and_moves_round_trip_through_event_payloads(slug: str) -> None:
    game = get_game(slug)
    trace = c.random_playout(game, game.min_seats, seed=21)
    state_type = type(trace[0][0])
    for state, move in trace[:: max(1, len(trace) // 10)]:
        env = make_event(
            "room-1",
            MoveApplied(seat=move.seat, move=move.model_dump(mode="json")),
            PUBLIC,
        )
        wire = EventEnvelope.model_validate_json(env.model_dump_json())
        assert game.parse_move(wire.payload["move"]) == move
        as_json = json.dumps(state.model_dump(mode="json"))
        assert state_type.model_validate(json.loads(as_json)) == state


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_rules_markdown_mentions_every_move_kind(slug: str) -> None:
    game = get_game(slug)
    rules = game.rules_markdown()
    trace = c.random_playout(game, game.min_seats, seed=2)
    kinds = {m.kind for _, m in trace}
    for kind in kinds:
        assert kind in rules, f"{slug} rules.md does not document move kind {kind!r}"
