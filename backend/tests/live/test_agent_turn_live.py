"""One real AgentController turn per configured Ollama model.

Deselected by default (``-m ollama``). Asserts the structured-output path
works end to end; a model is allowed to need repairs but must produce a
legal move within the repair budget, or the test records why it could not.
"""

from __future__ import annotations

import time

import pytest

from app.agents import AgentController, get_persona
from app.agents.controller import _compatible
from app.core.config import settings
from app.events import InMemoryEventBus
from app.games import get_game
from app.llm import probe_health
from app.llm.registry import ModelRegistry
from app.referee import ControllerError, TurnContext

pytestmark = pytest.mark.ollama


@pytest.fixture
async def registry() -> ModelRegistry:
    health = await probe_health(settings)
    if not health.reachable:
        pytest.skip(health.summary())
    return ModelRegistry.from_settings(settings)


@pytest.mark.parametrize("model", settings.LLM_MODELS)
async def test_one_real_turn(registry: ModelRegistry, model: str) -> None:
    game = get_game("uno_classic")
    state = game.new_game(42, 2)
    seat = game.current_seat(state)
    assert seat is not None
    view = game.player_view(state, seat)
    bus = InMemoryEventBus()
    cfg = registry.config_for(model)
    agent = AgentController(
        game,
        registry.client_for(model),
        model,
        get_persona("steady"),
        bus,
        "live",
        seat,
        max_repairs=2,
        num_ctx=cfg.num_ctx,
        keep_alive=cfg.keep_alive,
    )
    ctx = TurnContext(
        room_id="live",
        seat=seat,
        turn_id="t",
        attempt=1,
        deadline=1e12,
        last_error=None,
    )
    started = time.perf_counter()
    try:
        move = await agent.decide(view, ctx)
    except ControllerError as exc:
        pytest.fail(f"{model}: {exc}")
    elapsed = time.perf_counter() - started
    parsed = game.parse_move(move)
    legal = [m.model_dump(mode="json") for m in game.legal_moves(state, seat)]
    is_legal = any(_compatible(parsed.model_dump(mode="json"), m) for m in legal)
    print(
        f"\n{model}: {elapsed:.1f}s, legal={is_legal}, move={game.describe_move(parsed)}"
    )
    assert is_legal, f"{model} chose an illegal move: {move}"
    assert game.apply(state, parsed)  # the referee's real check
    reasoning = [
        e for e in await bus.history("live", since="0") if e.type == "agent.reasoning"
    ]
    assert reasoning, "no reasoning published"
    await registry.aclose()
