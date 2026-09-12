"""The referee drives every game type to completion through the same code path
whether seats are scripted, policy-driven or human (queue-fed)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.events import (
    EventEnvelope,
    InMemoryEventBus,
    Subscriber,
    filter_for_subscriber,
)
from app.games import GAME_CATALOG, get_game
from app.games.base import PlayerView
from app.referee import (
    GameRunner,
    HumanController,
    PolicyController,
    Referee,
    ScriptedController,
    SeatSpec,
    TurnContext,
    fallback_move,
)

SLUGS = sorted(GAME_CATALOG)


def specs(n: int, kind: str = "scripted", **kw: Any) -> list[SeatSpec]:
    return [SeatSpec(seat=i, kind=kind, player=f"p{i}", **kw) for i in range(n)]  # type: ignore[arg-type]


async def collect(bus: InMemoryEventBus, room: str) -> list[EventEnvelope]:
    return await bus.history(room, since="0", limit=100_000)


@pytest.mark.parametrize("slug", SLUGS)
async def test_policy_seats_finish_every_game(slug: str) -> None:
    game = get_game(slug)
    n = game.min_seats
    bus = InMemoryEventBus(maxlen=100_000)
    ref = Referee(
        game, bus, "r1", 7, specs(n), {i: PolicyController() for i in range(n)}
    )
    final = await asyncio.wait_for(ref.run(), 60)
    assert game.is_terminal(final)
    events = await collect(bus, "r1")
    types = [e.type for e in events]
    assert types[0] == "game.started"
    assert types[-1] == "game.finished"
    applied = [e for e in events if e.type == "game.move_applied"]
    assert len(applied) == len(ref.moves) == final.move_count
    assert [e.payload["move_index"] for e in applied] == list(range(len(applied)))
    # replay from the log reproduces the live state
    replayed = Referee.replay(slug, 7, n, [e.payload["move"] for e in applied])
    assert replayed == final


async def test_human_and_scripted_share_the_code_path() -> None:
    """Seat 0 is a human fed through the runner; seat 1 is scripted from the
    human's recorded moves of a first run. Both games end identically."""
    game = get_game("uno_classic")
    bus = InMemoryEventBus(maxlen=100_000)
    first = Referee(
        game, bus, "a", 3, specs(2), {0: PolicyController(), 1: PolicyController()}
    )
    final_a = await asyncio.wait_for(first.run(), 60)
    seat0_moves = [m for m in first.moves if m["seat"] == 0]
    seat1_moves = [m for m in first.moves if m["seat"] == 1]

    runner = GameRunner(bus)
    human = HumanController()
    running = runner.start(
        "b",
        "uno_classic",
        3,
        [
            SeatSpec(seat=0, kind="human", player="me", timeout_s=5),
            SeatSpec(seat=1, kind="scripted", player="bot"),
        ],
        {0: human, 1: ScriptedController(seat1_moves)},
    )

    # feed the human's moves as the referee asks for them
    async def feed() -> None:
        for m in seat0_moves:
            while (
                human.view is None
                or human.view.current_seat != 0
                or human.view.legal_moves == []
            ):
                await asyncio.sleep(0)
                if running.done:
                    return
            human.view = None
            runner.submit_move("b", 0, m)

    feeder = asyncio.create_task(feed())
    final_b = await asyncio.wait_for(running.task, 60)
    await feeder
    assert final_b == final_a


async def test_agent_retries_then_fallback() -> None:
    game = get_game("uno_classic")
    bus = InMemoryEventBus(maxlen=10_000)
    bad = ScriptedController([{"kind": "play", "seat": 0, "card_index": 99}] * 10)
    blocker = HumanController()  # seat 1 never answers, so the game pauses after seat 0
    ref = Referee(
        game,
        bus,
        "r",
        1,
        [
            SeatSpec(seat=0, kind="agent", player="a", max_retries=2),
            SeatSpec(seat=1, kind="human", player="b", timeout_s=60),
        ],
        {0: bad, 1: blocker},
    )
    task = asyncio.create_task(ref.run())
    while blocker.view is None:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    events = await collect(bus, "r")
    rejected = [e for e in events if e.type == "game.move_rejected"]
    assert len(rejected) == 3  # attempts 1..3, then fallback
    assert all(e.visibility.kind == "seat" for e in rejected)
    assert rejected[0].payload["reason"]
    assert bad.calls == 3
    assert ref.moves
    assert ref.moves[0]["seat"] == 0
    assert events[-1].type == "room.stopped"


async def test_human_retries_until_deadline_then_fallback() -> None:
    game = get_game("uno_classic")
    bus = InMemoryEventBus(maxlen=10_000)
    human = HumanController()
    ref = Referee(
        game,
        bus,
        "r",
        1,
        [
            SeatSpec(seat=0, kind="human", player="h", timeout_s=0.3),
            SeatSpec(seat=1, kind="scripted", player="b"),
        ],
        {0: human, 1: PolicyController()},
    )
    task = asyncio.create_task(ref.run())
    while human.view is None:
        await asyncio.sleep(0)
    human.submit(
        {"kind": "play", "seat": 0, "card_index": 99}
    )  # rejected, keeps waiting
    while len(ref.moves) < 1:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    events = await collect(bus, "r")
    assert any(e.type == "game.move_rejected" for e in events)
    assert any(e.type == "game.turn_timed_out" for e in events)
    assert human.last_error is not None


@pytest.mark.parametrize("slug", SLUGS)
async def test_player_subscriber_never_sees_other_seats(slug: str) -> None:
    game = get_game(slug)
    n = game.min_seats
    bus = InMemoryEventBus(maxlen=100_000)
    ref = Referee(
        game, bus, "p", 11, specs(n), {i: PolicyController() for i in range(n)}
    )
    await asyncio.wait_for(ref.run(), 60)
    events = await collect(bus, "p")
    for s in range(n):
        mine = [
            filter_for_subscriber(e, Subscriber(kind="player", seat=s)) for e in events
        ]
        for e in filter(None, mine):
            if e.type in {"game.view_updated", "game.move_rejected"}:
                assert e.payload["seat"] == s
    privileged = [
        filter_for_subscriber(e, Subscriber(kind="privileged", seat=None))
        for e in events
    ]
    assert sum(
        e is not None and e.type == "game.view_updated" for e in privileged
    ) == n * (len(ref.moves) + 1)


async def test_runner_stop_publishes_room_stopped() -> None:
    bus = InMemoryEventBus(maxlen=10_000)
    runner = GameRunner(bus)
    human = HumanController()
    running = runner.start(
        "s",
        "hearts",
        1,
        [
            SeatSpec(seat=0, kind="human", player="h", timeout_s=30),
            *[SeatSpec(seat=i, kind="scripted", player="b") for i in (1, 2)],
        ],
        {0: human, 1: PolicyController(), 2: PolicyController()},
    )
    while human.view is None:
        await asyncio.sleep(0)
    await runner.stop("s", "admin")
    assert running.done
    assert (await collect(bus, "s"))[-1].type == "room.stopped"
    assert runner.active() == []
    with pytest.raises(TypeError):
        runner.submit_move("s", 1, {})


def test_fallback_is_first_legal_move() -> None:
    game = get_game("hearts")
    state = game.new_game(1, 4)
    assert fallback_move(game, state, 0) == game.legal_moves(state, 0)[0]


class TalkativePolicy(PolicyController):
    """A controller with the optional chat hooks; ``say`` publishes nothing itself."""

    def __init__(self) -> None:
        super().__init__()
        self.heard: list[str] = []
        self.said = 0

    async def say(self, view: PlayerView, description: str) -> str | None:
        self.said += 1
        return f"I just did: {description}" if self.said % 2 else None

    def hear(self, messages: list[Any]) -> None:
        self.heard.extend(m.text for m in messages)


async def test_table_talk_hooks_are_optional_and_fanned_out() -> None:
    game = get_game("hearts")
    bus = InMemoryEventBus(maxlen=100_000)
    talkers = {0: TalkativePolicy(), 1: TalkativePolicy(), 2: PolicyController()}
    ref = Referee(game, bus, "t", 5, specs(3), talkers)  # type: ignore[arg-type]
    await asyncio.wait_for(ref.run(), 60)
    assert talkers[0].said > 0
    # seat 1 heard seat 0's lines and never its own
    assert talkers[1].heard
    assert all(line.startswith("I just did") for line in talkers[1].heard)
    assert len(talkers[1].heard) == (talkers[0].said + 1) // 2
    # chat never entered the event log from the referee: the controller owns publishing
    assert not any(e.type == "chat.message" for e in await collect(bus, "t"))


async def test_parseable_but_illegal_move_leaves_state_untouched() -> None:
    game = get_game("hearts")
    bus = InMemoryEventBus(maxlen=10_000)
    state0 = game.new_game(1, 3)
    legal = game.legal_moves(state0, 0)[0].model_dump(mode="json")
    illegal = legal | {"cards": [0, 0, 0]}  # parses, apply rejects (duplicates)
    script = ScriptedController([illegal, legal])
    ref = Referee(
        game,
        bus,
        "x",
        1,
        [SeatSpec(seat=0, kind="agent", player="a", max_retries=2), *specs(3)[1:]],
        {0: script, 1: HumanController(), 2: HumanController()},
    )
    task = asyncio.create_task(ref.run())
    while len(ref.moves) < 1:
        await asyncio.sleep(0)
    assert ref.moves[0] == legal
    rejected = [e for e in await collect(bus, "x") if e.type == "game.move_rejected"]
    assert len(rejected) == 1
    assert "distinct" in rejected[0].payload["reason"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_game_module_error_ends_room_visibly() -> None:
    class Broken(PolicyController):
        async def decide(self, view: PlayerView, ctx: TurnContext) -> dict[str, Any]:
            raise ZeroDivisionError("controller bug")

    game = get_game("hearts")
    bus = InMemoryEventBus(maxlen=10_000)
    ref = Referee(
        game,
        bus,
        "e",
        1,
        specs(3),
        {0: Broken(), 1: PolicyController(), 2: PolicyController()},
    )
    with pytest.raises(ZeroDivisionError):
        await ref.run()
    last = (await collect(bus, "e"))[-1]
    assert last.type == "room.stopped"
    assert last.payload["reason"].startswith("error:")
