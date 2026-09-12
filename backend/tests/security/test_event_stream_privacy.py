"""A player subscriber's filtered stream never carries another seat's hand."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.events import (
    EventEnvelope,
    InMemoryEventBus,
    Subscriber,
    filter_for_subscriber,
)
from app.games import GAME_CATALOG, get_game
from app.referee import PolicyController, Referee, SeatSpec

SLUGS = sorted(GAME_CATALOG)


def _compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"))


async def _run(slug: str) -> tuple[int, list[EventEnvelope]]:
    game = get_game(slug)
    n = game.min_seats
    bus = InMemoryEventBus(maxlen=100_000)
    specs = [SeatSpec(seat=i, kind="scripted", player=f"p{i}") for i in range(n)]
    ref = Referee(
        game, bus, "sec", 17, specs, {i: PolicyController() for i in range(n)}
    )
    await asyncio.wait_for(ref.run(), 60)
    return n, await bus.history("sec", since="0", limit=100_000)


@pytest.mark.parametrize("slug", SLUGS)
async def test_player_stream_carries_only_own_hand(slug: str) -> None:
    n, events = await _run(slug)
    privileged = [
        e
        for e in (
            filter_for_subscriber(e, Subscriber(kind="privileged")) for e in events
        )
        if e is not None and e.type == "game.view_updated"
    ]
    assert len(privileged) % n == 0
    assert len(privileged) >= 2 * n
    # views are published seat 0..n-1 after every move: batches of n
    batches = [privileged[i : i + n] for i in range(0, len(privileged), n)]
    for batch in batches:
        assert [e.payload["seat"] for e in batch] == list(range(n))
    hands = [[e.payload["view"]["hand"] for e in batch] for batch in batches]

    for s in range(n):
        sub = Subscriber(kind="player", seat=s)
        mine = [
            e for e in (filter_for_subscriber(e, sub) for e in events) if e is not None
        ]
        assert any(e.type == "game.view_updated" for e in mine)
        for e in mine:
            if e.type in {"game.view_updated", "game.move_rejected"}:
                assert e.payload["seat"] == s
            assert e.visibility.kind != "privileged"
        seen_views = [e for e in mine if e.type == "game.view_updated"]
        assert len(seen_views) == len(batches)
        # Views compare within their own batch: No Mercy's 7/0 rule legitimately
        # hands one seat's cards to another on the next move.
        for own, batch_hands in zip(seen_views, hands, strict=True):
            payload = _compact(own.payload)
            for t, hand in enumerate(batch_hands):
                if t != s and len(hand) >= 2:
                    assert _compact(hand) not in payload, (
                        f"seat {t}'s hand reached seat {s}"
                    )
        rest = "\n".join(
            _compact(e.payload) for e in mine if e.type != "game.view_updated"
        )
        for batch_hands in hands:
            for t, hand in enumerate(batch_hands):
                if t != s and len(hand) >= 2:
                    assert _compact(hand) not in rest, (
                        f"seat {t}'s hand reached seat {s}"
                    )
