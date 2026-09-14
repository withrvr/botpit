"""Prometheus metrics (Phase 13, DECISIONS.md §14). Pure/unit-level checks
that don't need a real Room/RoomService/Redis -- those live alongside the
fixtures they need (``ACTIVE_GAMES``/``MOVES_TOTAL`` in
``tests/rooms/test_room_service.py``, the ``ws.session`` wide event and
``ACTIVE_WS_CONNECTIONS`` in ``tests/rooms/test_rooms_ws.py``).
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.agents import AgentController, get_persona
from app.events import InMemoryEventBus
from app.games import get_game
from app.llm.fake import FakeLLMClient
from app.metrics import (
    AGENT_TURN_ERRORS_TOTAL,
    AGENT_TURN_LATENCY_SECONDS,
    AGENT_TURN_TIMEOUTS_TOTAL,
    MODEL_SWAPS_TOTAL,
    REGISTRY,
    note_llm_call,
)
from app.referee import PolicyController, Referee, SeatSpec, TurnContext
from app.telemetry import wide_event


def test_metrics_route_returns_prometheus_text(client: TestClient) -> None:
    r = client.get("/api/v1/metrics")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    # Unlabeled gauges/counters are always present once registered, even at
    # zero; labeled ones (agent turn latency, swaps) only appear once
    # observed at least once, so only assert the always-present ones here.
    assert "botpit_active_games" in body
    assert "botpit_active_ws_connections" in body
    assert "botpit_moves_total" in body


def test_note_llm_call_counts_a_swap_only_when_model_changes() -> None:
    base_url = "http://fake-swap-test:1"  # unique per test, never reused elsewhere
    before = MODEL_SWAPS_TOTAL.labels(base_url=base_url)._value.get()

    note_llm_call(base_url, "model-a")
    note_llm_call(base_url, "model-a")  # same model: not a swap
    assert MODEL_SWAPS_TOTAL.labels(base_url=base_url)._value.get() == before

    note_llm_call(base_url, "model-b")  # different model: a swap
    assert MODEL_SWAPS_TOTAL.labels(base_url=base_url)._value.get() == before + 1

    note_llm_call(base_url, "model-a")  # swapped back: still a swap
    assert MODEL_SWAPS_TOTAL.labels(base_url=base_url)._value.get() == before + 2


async def test_agent_turn_latency_and_error_metrics_recorded_via_decide() -> None:
    game = get_game("uno_classic")
    state = game.new_game(1, 2)
    seat = game.current_seat(state)
    assert seat is not None
    view = game.player_view(state, seat)
    bus = InMemoryEventBus()

    model = "observability-test-model"
    latency_before = AGENT_TURN_LATENCY_SECONDS.labels(model=model)._sum.get()
    error_before = AGENT_TURN_ERRORS_TOTAL.labels(
        model=model, error="bad_json"
    )._value.get()

    # First reply fails `_parse_answer` (not JSON at all) -- records a
    # `latency_ms` observation and an `error="bad_json"` count via
    # `_metrics` -- then the repair retry gets a real legal move, so
    # `decide()` completes normally instead of raising.
    good_reply = json.dumps({"reasoning": "ok", "move": view.legal_moves[0]})
    client = FakeLLMClient(["not json", good_reply], latency_s=0.01)
    agent = AgentController(game, client, model, get_persona("steady"), bus, "r", seat)
    ctx = TurnContext(
        room_id="r", seat=seat, turn_id="t", attempt=1, deadline=1e9, last_error=None
    )
    with wide_event("game.turn"):
        move = await agent.decide(view, ctx)

    assert move == view.legal_moves[0]
    assert AGENT_TURN_LATENCY_SECONDS.labels(model=model)._sum.get() > latency_before
    assert (
        AGENT_TURN_ERRORS_TOTAL.labels(model=model, error="bad_json")._value.get()
        == error_before + 1
    )


async def test_agent_turn_timeout_increments_the_counter() -> None:
    """The referee's own turn deadline (not `AgentController` itself) is
    what times out -- needs a real `Referee.run()`, mirroring
    `test_human_retries_until_deadline_then_fallback` but for an agent seat."""
    game = get_game("uno_classic")
    bus = InMemoryEventBus(maxlen=10_000)
    model = "observability-timeout-model"
    slow_client = FakeLLMClient(latency_s=5.0)  # far past the seat's own timeout
    agent = AgentController(
        game, slow_client, model, get_persona("steady"), bus, "r", 1
    )
    ref = Referee(
        game,
        bus,
        "r",
        1,
        [
            SeatSpec(seat=0, kind="scripted", player="b"),
            SeatSpec(seat=1, kind="agent", player="a", model=model, timeout_s=0.05),
        ],
        {0: PolicyController(), 1: agent},
    )
    before = AGENT_TURN_TIMEOUTS_TOTAL.labels(model=model)._value.get()
    task = asyncio.create_task(ref.run())
    for _ in range(500):
        if AGENT_TURN_TIMEOUTS_TOTAL.labels(model=model)._value.get() > before:
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        raise AssertionError("agent turn never timed out within 5s")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert AGENT_TURN_TIMEOUTS_TOTAL.labels(model=model)._value.get() == before + 1


def test_registry_only_exposes_botpit_metrics() -> None:
    """The dedicated registry (not `prometheus_client`'s process-global
    default) means `/metrics` never leaks unrelated process metrics."""
    names = {m.name for m in REGISTRY.collect()}
    assert all(name.startswith("botpit_") for name in names)
