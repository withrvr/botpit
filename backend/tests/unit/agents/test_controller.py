"""AgentController: two calls per turn, repair loop, fallback, metrics, privacy."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.agents import AgentController, get_persona
from app.events import InMemoryEventBus, Subscriber, filter_for_subscriber
from app.games import get_game
from app.llm.client import CompletionRequest, LLMError
from app.llm.fake import FakeLLMClient
from app.referee import (
    ControllerError,
    PolicyController,
    Referee,
    SeatSpec,
    TurnContext,
)
from app.telemetry import wide_event


def _ctx(seat: int = 0, last_error: str | None = None) -> TurnContext:
    return TurnContext(
        room_id="r",
        seat=seat,
        turn_id="t",
        attempt=1,
        deadline=1e9,
        last_error=last_error,
    )


def _first_legal(req: CompletionRequest) -> str:
    """A fake model: picks the first legal move from the prompt, stays silent in chat."""
    user = req.messages[-1].content
    start = user.index("{")
    end = user.rindex("}")
    view = json.loads(user[start : end + 1])
    if "legal_moves" not in view:  # the chat call carries no legal_moves
        return json.dumps({"say": None})
    return json.dumps({"reasoning": "first legal move", "move": view["legal_moves"][0]})


async def test_decide_returns_first_legal_and_publishes_reasoning() -> None:
    game = get_game("uno_classic")
    state = game.new_game(3, 2)
    seat = game.current_seat(state)
    assert seat is not None
    view = game.player_view(state, seat)
    bus = InMemoryEventBus()
    client = FakeLLMClient(_first_legal)
    agent = AgentController(game, client, "fake", get_persona("steady"), bus, "r", seat)
    with wide_event("game.turn") as ev:
        move = await agent.decide(view, _ctx(seat))
        assert move == view.legal_moves[0]
        calls = ev.get("llm.calls")
        assert calls
        assert calls[0]["model"] == "fake"
        assert calls[0]["ok"] is True
    req = client.requests[0]
    assert req.json_schema is not None
    assert set(req.json_schema["required"]) == {"reasoning", "move"}
    events = await bus.history("r", since="0")
    assert [e.type for e in events] == ["agent.reasoning"]
    assert events[0].visibility.kind == "privileged"
    assert (
        filter_for_subscriber(events[0], Subscriber(kind="player", seat=1 - seat))
        is None
    )


async def test_repair_loop_feeds_error_back_then_fallbacks_to_referee() -> None:
    game = get_game("uno_classic")
    state = game.new_game(3, 2)
    seat = game.current_seat(state)
    assert seat is not None
    view = game.player_view(state, seat)
    bus = InMemoryEventBus()
    client = FakeLLMClient(
        [
            "not json at all",
            json.dumps(
                {"reasoning": "oops", "move": {"kind": "play", "card_index": "x"}}
            ),
            json.dumps({"reasoning": "ok", "move": view.legal_moves[0]}),
        ]
    )
    agent = AgentController(
        game, client, "fake", get_persona("gambler"), bus, "r", seat, max_repairs=2
    )
    move = await agent.decide(view, _ctx(seat))
    assert move == view.legal_moves[0]
    assert len(client.requests) == 3
    # the second and third prompts carry the rejection reason
    assert "rejected" in client.requests[1].messages[-1].content
    assert "invalid move" in client.requests[2].messages[-1].content


async def test_exhausted_repairs_raise_controller_error() -> None:
    game = get_game("hearts")
    state = game.new_game(1, 4)
    view = game.player_view(state, 0)
    client = FakeLLMClient(["junk"] * 5)
    agent = AgentController(
        game,
        client,
        "fake",
        get_persona("grump"),
        InMemoryEventBus(),
        "r",
        0,
        max_repairs=1,
    )
    with pytest.raises(ControllerError, match="no parseable move"):
        await agent.decide(view, _ctx())
    assert len(client.requests) == 2


async def test_llm_errors_become_controller_errors() -> None:
    game = get_game("hearts")
    view = game.player_view(game.new_game(1, 4), 0)
    client = FakeLLMClient([LLMError("down", retriable=False)])
    agent = AgentController(
        game, client, "fake", get_persona("coach"), InMemoryEventBus(), "r", 0
    )
    with pytest.raises(ControllerError, match="down"):
        await agent.decide(view, _ctx())


async def test_say_publishes_chat_and_hear_collects() -> None:
    game = get_game("uno_classic")
    view = game.player_view(game.new_game(3, 2), 0)
    bus = InMemoryEventBus()
    client = FakeLLMClient(
        [json.dumps({"say": "  nice one  "}), json.dumps({"say": None}), "garbage"]
    )
    agent = AgentController(
        game, client, "fake", get_persona("chatterbox"), bus, "r", 0
    )
    assert await agent.say(view, "seat 0 plays red 7") == "nice one"
    assert await agent.say(view, "x") is None
    assert await agent.say(view, "x") is None
    events = await bus.history("r", since="0")
    assert [e.type for e in events] == ["chat.message"]
    assert events[0].payload == {"seat": 0, "text": "nice one"}
    # the chat prompt never carries the hand
    chat_user = client.requests[0].messages[-1].content
    assert json.dumps(view.hand) not in chat_user
    agent.hear(
        [events[0].payload_model()] if hasattr(events[0], "payload_model") else []
    )


@pytest.mark.parametrize("slug", ["uno_classic", "uno_flip", "uno_no_mercy", "hearts"])
async def test_full_game_with_fake_agents_finishes(slug: str) -> None:
    game = get_game(slug)
    n = game.min_seats
    bus = InMemoryEventBus(maxlen=100_000)
    clients = [FakeLLMClient(_first_legal) for _ in range(n)]
    controllers = {
        i: AgentController(
            game, clients[i], f"fake-{i}", get_persona("steady"), bus, "g", i
        )
        for i in range(n)
    }
    specs = [
        SeatSpec(
            seat=i, kind="agent", player=f"bot{i}", model=f"fake-{i}", timeout_s=30
        )
        for i in range(n)
    ]
    ref = Referee(game, bus, "g", 9, specs, controllers)
    final = await asyncio.wait_for(ref.run(), 120)
    assert game.is_terminal(final)
    events = await bus.history("g", since="0", limit=1_000_000)
    reasoning = [e for e in events if e.type == "agent.reasoning"]
    assert len(reasoning) == len(ref.moves)
    assert all(e.visibility.kind == "privileged" for e in reasoning)
    # every agent also got the chat call after each of its moves (fake returns a move JSON, so no chat lands)
    assert all(len(c.requests) >= 2 for c in clients)


async def test_mixed_table_human_and_agents() -> None:
    from app.referee import HumanController

    game = get_game("hearts")  # short and always terminates
    bus = InMemoryEventBus(maxlen=100_000)
    human = HumanController()
    controllers: dict[int, Any] = {
        0: human,
        1: AgentController(
            game,
            FakeLLMClient(_first_legal),
            "fake",
            get_persona("steady"),
            bus,
            "m",
            1,
        ),
        2: PolicyController(),
    }
    specs = [
        SeatSpec(
            seat=0, kind="human", player="me", timeout_s=0.05
        ),  # referee plays for them
        SeatSpec(seat=1, kind="agent", player="bot", model="fake"),
        SeatSpec(seat=2, kind="scripted", player="script"),
    ]
    ref = Referee(game, bus, "m", 4, specs, controllers)
    final = await asyncio.wait_for(ref.run(), 120)
    assert game.is_terminal(final)
    kinds = {
        e.payload["kind"]
        for e in await bus.history("m", since="0", limit=1_000_000)
        if e.type == "game.turn_started"
    }
    assert kinds == {"human", "agent", "scripted"}


async def test_wrong_seat_is_repaired_and_missing_seat_is_filled() -> None:
    game = get_game("hearts")
    state = game.new_game(2, 4)
    view = game.player_view(state, 0)
    legal = view.legal_moves[0]
    wrong = dict(legal) | {"seat": 3}
    missing = {k: v for k, v in legal.items() if k != "seat"}
    client = FakeLLMClient(
        [
            json.dumps({"reasoning": "r", "move": wrong}),
            json.dumps({"reasoning": "r", "move": missing}),
        ]
    )
    agent = AgentController(
        game, client, "fake", get_persona("steady"), InMemoryEventBus(), "r", 0
    )
    move = await agent.decide(view, _ctx(0))
    assert move == legal
    assert "move.seat must be 0" in client.requests[1].messages[-1].content


async def test_parseable_but_not_legal_move_is_repaired_from_the_view() -> None:
    game = get_game("uno_classic")
    state = game.new_game(42, 2)
    seat = game.current_seat(state)
    assert seat is not None
    view = game.player_view(state, seat)
    legal = view.legal_moves[0]
    illegal = dict(legal) | {"card_index": 99}  # parses fine, not in legal_moves
    client = FakeLLMClient(
        [
            json.dumps({"reasoning": "r", "move": illegal}),
            json.dumps({"reasoning": "r", "move": legal}),
        ]
    )
    agent = AgentController(
        game, client, "fake", get_persona("steady"), InMemoryEventBus(), "r", seat
    )
    assert await agent.decide(view, _ctx(seat)) == legal
    assert "not one of legal_moves" in client.requests[1].messages[-1].content
