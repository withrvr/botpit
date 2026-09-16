"""RoomService: create/join/leave/start/stop/capacity (docs/rooms.md).

Marked ``redis`` (real Redis, no fakeredis -- see ``tests/rooms/conftest.py``)
since ``RoomService`` always needs a real ``JoinQueue`` for its constructor,
even on tests that never hit a capacity/queue branch.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

import pytest
from redis.asyncio import Redis
from sqlmodel import Session

from app import settings_store
from app.authz import load_permissions
from app.authz.permissions import P
from app.core.config import settings
from app.events import InMemoryEventBus
from app.llm import ModelRegistry
from app.metrics import ACTIVE_GAMES, MOVES_TOTAL
from app.models import RoomCreate, RoomSeatSpec
from app.referee import GameRunner
from app.rooms.codes import is_valid_code
from app.rooms.queue import JoinQueue
from app.rooms.service import (
    CapacityExceededError,
    RoomFullError,
    RoomNotWaitingError,
    RoomService,
)
from tests.utils.user import create_random_user

pytestmark = pytest.mark.redis


def _two_human(
    visibility: Literal["private", "unlisted", "public"] = "private",
) -> RoomCreate:
    return RoomCreate(
        game="uno_classic",
        seat_count=2,
        visibility=visibility,
        seats=[RoomSeatSpec(kind="human"), RoomSeatSpec(kind="human")],
    )


def _two_agent() -> RoomCreate:
    return RoomCreate(
        game="uno_classic",
        seat_count=2,
        visibility="public",
        seats=[RoomSeatSpec(kind="agent"), RoomSeatSpec(kind="agent")],
    )


async def _wait_for_started(
    bus: InMemoryEventBus, room_id: str, deadline_s: float = 5.0
) -> None:
    async def poll() -> None:
        while True:
            events = await bus.history(room_id, since="0")
            if any(e.type == "game.started" for e in events):
                return
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), deadline_s)


# -- create -------------------------------------------------------------


async def test_create_seats_match_spec_and_code_round_trips(
    db: Session, service: RoomService
) -> None:
    user = create_random_user(db)
    room = await service.create(user, _two_human())
    seats = service.seats(room.id)
    assert [s.seat for s in seats] == [0, 1]
    assert all(s.kind == "human" and s.status == "open" for s in seats)
    assert is_valid_code(room.code)
    assert room.seed >= 0


async def test_create_grants_creator_moderator_role(
    db: Session, service: RoomService
) -> None:
    user = create_random_user(db)
    room = await service.create(user, _two_human())
    perms = load_permissions(db, user.id, room.id)
    assert P.ROOM_FORCE_STOP in perms


async def test_create_publishes_room_created(
    db: Session, service: RoomService, bus: InMemoryEventBus
) -> None:
    user = create_random_user(db)
    room = await service.create(user, _two_human())
    events = await bus.history(str(room.id), since="0")
    assert events[0].type == "room.created"
    assert events[0].payload == {"game": "uno_classic", "seats": 2}


async def test_create_non_creator_lacking_permission_is_not_this_layer(
    db: Session, service: RoomService
) -> None:
    """``RoomService.create`` itself never checks ``room:create``: the route
    dependency does. This is just documenting/confirming that split."""
    user = create_random_user(db)
    room = await service.create(user, _two_human())
    assert room.creator_id == user.id


async def test_create_unknown_game_is_value_error(
    db: Session, service: RoomService
) -> None:
    user = create_random_user(db)
    with pytest.raises(ValueError):
        await service.create(
            user,
            RoomCreate(
                game="nope",
                seat_count=2,
                seats=[RoomSeatSpec(kind="human"), RoomSeatSpec(kind="human")],
            ),
        )


async def test_create_bad_seat_count_is_value_error(
    db: Session, service: RoomService
) -> None:
    """Hearts takes 3-6 seats; 2 is out of range."""
    user = create_random_user(db)
    with pytest.raises(ValueError):
        await service.create(
            user,
            RoomCreate(
                game="hearts",
                seat_count=2,
                seats=[RoomSeatSpec(kind="human"), RoomSeatSpec(kind="human")],
            ),
        )


async def test_create_unknown_model_is_value_error(
    db: Session, service: RoomService
) -> None:
    user = create_random_user(db)
    with pytest.raises(ValueError):
        await service.create(
            user,
            RoomCreate(
                game="uno_classic",
                seat_count=2,
                seats=[
                    RoomSeatSpec(kind="agent", model="no-such-model"),
                    RoomSeatSpec(kind="human"),
                ],
            ),
        )


async def test_create_unknown_persona_is_value_error(
    db: Session, service: RoomService
) -> None:
    user = create_random_user(db)
    with pytest.raises(ValueError):
        await service.create(
            user,
            RoomCreate(
                game="uno_classic",
                seat_count=2,
                seats=[
                    RoomSeatSpec(kind="agent", persona="no-such-persona"),
                    RoomSeatSpec(kind="human"),
                ],
            ),
        )


async def test_create_unknown_invited_user_is_value_error(
    db: Session, service: RoomService
) -> None:
    user = create_random_user(db)
    with pytest.raises(ValueError):
        await service.create(
            user,
            RoomCreate(
                game="uno_classic",
                seat_count=2,
                seats=[
                    RoomSeatSpec(kind="human", invited_user_id=uuid.uuid4()),
                    RoomSeatSpec(kind="human"),
                ],
            ),
        )


def test_seats_length_mismatch_is_a_pydantic_validation_error() -> None:
    """Confirms this already 422s via ``RoomCreate``'s own model validator --
    ``RoomService.create`` never even sees a mismatched list."""
    with pytest.raises(ValueError):
        RoomCreate(game="uno_classic", seat_count=2, seats=[RoomSeatSpec(kind="human")])


# -- join / leave ---------------------------------------------------------


async def test_join_fills_lowest_numbered_open_seat(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    joiner = create_random_user(db)
    seat = await service.join_by_code(joiner, room.code)
    assert seat.seat == 0
    assert seat.user_id == joiner.id
    assert seat.status == "filled"


async def test_join_prefers_invited_seat_over_lower_numbered_open_one(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    invitee = create_random_user(db)
    room = await service.create(
        creator,
        RoomCreate(
            game="uno_classic",
            seat_count=2,
            seats=[
                RoomSeatSpec(kind="human"),
                RoomSeatSpec(kind="human", invited_user_id=invitee.id),
            ],
        ),
    )
    seat = await service.join_by_code(invitee, room.code)
    assert seat.seat == 1


async def test_join_is_idempotent(db: Session, service: RoomService) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    joiner = create_random_user(db)
    first = await service.join_by_code(joiner, room.code)
    second = await service.join_by_code(joiner, room.code)
    assert first.id == second.id


async def test_join_full_room_raises_with_room_id(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    for _ in range(2):
        await service.join_by_code(create_random_user(db), room.code)
    with pytest.raises(RoomFullError) as exc_info:
        await service.join_by_code(create_random_user(db), room.code)
    assert exc_info.value.room_id == room.id


async def test_join_unknown_code_is_lookup_error(
    db: Session, service: RoomService
) -> None:
    user = create_random_user(db)
    with pytest.raises(LookupError):
        await service.join_by_code(user, "NOTREAL1")


async def test_join_non_waiting_room_raises(db: Session, service: RoomService) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_agent())
    await service.start(room.id)
    try:
        with pytest.raises(RoomNotWaitingError):
            await service.join_by_code(create_random_user(db), room.code)
    finally:
        await service.stop(room.id, "test cleanup")


async def test_leave_frees_the_seat(db: Session, service: RoomService) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    joiner = create_random_user(db)
    await service.join_by_code(joiner, room.code)
    await service.leave(joiner, room.id)
    seat = next(s for s in service.seats(room.id) if s.seat == 0)
    assert seat.status == "open"
    assert seat.user_id is None


async def test_leave_is_a_noop_when_not_seated(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    await service.leave(create_random_user(db), room.id)  # must not raise


async def test_leave_blocked_once_in_progress(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_agent())
    await service.start(room.id)
    try:
        await service.leave(creator, room.id)  # no-op: room is in_progress
        seats = service.seats(room.id)
        assert all(s.status == "filled" for s in seats)
    finally:
        await service.stop(room.id, "test cleanup")


# -- start / stop ---------------------------------------------------------


async def test_start_with_an_open_seat_raises(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    with pytest.raises(RoomFullError):
        await service.start(room.id)


async def test_force_start_backfills_open_seats_and_runs_a_real_game(
    db: Session,
    service: RoomService,
    runner: GameRunner,
    bus: InMemoryEventBus,
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    started = await service.force_start(creator, room.id)
    assert started.status == "in_progress"
    assert all(s.kind == "agent" for s in service.seats(room.id))
    running = runner.get(str(room.id))
    assert running is not None
    await _wait_for_started(bus, str(room.id))
    await service.stop(room.id, "test cleanup")
    assert runner.active() == []
    assert service.get(room.id).status == "stopped"


async def test_all_agent_room_starts_directly(
    db: Session, service: RoomService, runner: GameRunner
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_agent())
    started = await service.start(room.id)
    assert started.status == "in_progress"
    assert runner.get(str(room.id)) is not None
    await service.stop(room.id, "test cleanup")
    assert runner.active() == []


# -- observability (Phase 13) --------------------------------------------------


async def test_active_games_gauge_tracks_start_and_finish(
    db: Session, service: RoomService, runner: GameRunner
) -> None:
    before = ACTIVE_GAMES._value.get()
    creator = create_random_user(db)
    room = await service.create(creator, _two_agent())
    await service.start(room.id)
    assert ACTIVE_GAMES._value.get() == before + 1

    await service.stop(room.id, "test cleanup")
    # `_finish_room` (the decrement) races `stop()`'s own return, per its own
    # docstring -- poll rather than assume it already ran.
    for _ in range(200):
        if ACTIVE_GAMES._value.get() == before:
            break
        await asyncio.sleep(0.01)
    assert ACTIVE_GAMES._value.get() == before


async def test_moves_total_increments_when_a_move_is_submitted(
    db: Session, service: RoomService, runner: GameRunner
) -> None:
    creator = create_random_user(db)
    other = create_random_user(db)
    room = await service.create(
        creator,
        RoomCreate(
            game="uno_classic",
            seat_count=2,
            visibility="private",
            seats=[
                RoomSeatSpec(kind="human", user_id=creator.id),
                RoomSeatSpec(kind="human"),
            ],
        ),
    )
    await service.join_by_code(other, room.code)
    await service.start(room.id)

    running = runner.get(str(room.id))
    current_seat = running.game.current_seat(running.state)
    assert current_seat is not None
    current_user = next(
        creator if s.user_id == creator.id else other
        for s in service.seats(room.id)
        if s.seat == current_seat
    )
    view = running.game.player_view(running.state, current_seat)

    before = MOVES_TOTAL._value.get()
    service.submit_move(current_user, room.id, view.legal_moves[0])
    for _ in range(200):
        if MOVES_TOTAL._value.get() > before:
            break
        await asyncio.sleep(0.01)
    assert MOVES_TOTAL._value.get() == before + 1

    await service.stop(room.id, "test cleanup")


# -- chat (Phase 11) ----------------------------------------------------------


def _human_and_agent(creator_id: uuid.UUID) -> RoomCreate:
    return RoomCreate(
        game="uno_classic",
        seat_count=2,
        visibility="private",
        seats=[
            RoomSeatSpec(kind="human", user_id=creator_id),
            RoomSeatSpec(kind="agent"),
        ],
    )


async def test_send_chat_reaches_the_agent_seats_hear(
    db: Session, service: RoomService, runner: GameRunner
) -> None:
    """``send_chat`` feeds the message to every AI seat's ``hear()`` --
    ``AgentController.hear`` existed but nothing called it before this, so
    a human's chat never reached an agent's ``recent_chat`` at all. This
    is a pure, synchronous effect of ``hear()`` itself -- no real LLM call
    (``decide``/``say``) is needed to observe it."""
    creator = create_random_user(db)
    room = await service.create(creator, _human_and_agent(creator.id))
    started = await service.start(room.id)
    assert started.status == "in_progress"

    await service.send_chat(creator, room.id, "good luck!")

    running = runner.get(str(room.id))
    agent_seat = next(s.seat for s in service.seats(room.id) if s.kind == "agent")
    controller = running.controllers[agent_seat]
    assert any(m.text == "good luck!" for m in controller.recent_chat)

    await service.stop(room.id, "test cleanup")


async def test_stop_on_a_room_that_never_started_is_a_safe_noop(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    stopped = await service.stop(room.id, "operator stop")
    assert stopped.status == "stopped"
    assert stopped.finished_at is not None


# -- capacity ---------------------------------------------------------------


async def test_capacity_check_below_and_at_the_limit(
    db: Session, service: RoomService
) -> None:
    settings_store.set_value(db, "max_concurrent_games", 1, None)
    try:
        await service.capacity_check()  # 0 in_progress < 1: fine
        creator = create_random_user(db)
        room = await service.create(creator, _two_agent())
        await service.start(room.id)
        try:
            with pytest.raises(CapacityExceededError) as exc_info:
                await service.capacity_check()
            assert exc_info.value.scope == "global"
            assert exc_info.value.status.length == 0
        finally:
            await service.stop(room.id, "test cleanup")
    finally:
        settings_store.set_value(
            db, "max_concurrent_games", settings.MAX_CONCURRENT_GAMES, None
        )


async def test_create_at_capacity_raises_capacity_exceeded(
    db: Session, service: RoomService
) -> None:
    settings_store.set_value(db, "max_concurrent_games", 1, None)
    room = None
    try:
        creator = create_random_user(db)
        room = await service.create(creator, _two_agent())
        await service.start(room.id)
        with pytest.raises(CapacityExceededError):
            await service.create(creator, _two_agent())
    finally:
        if room is not None:
            await service.stop(room.id, "test cleanup")
        settings_store.set_value(
            db, "max_concurrent_games", settings.MAX_CONCURRENT_GAMES, None
        )


async def test_start_enforces_capacity_not_just_create(
    db: Session, service: RoomService
) -> None:
    """The actual fix for the exhaustion bug: capacity is re-checked at
    start(), not only at create(). Without this, a caller could accumulate
    many "waiting" rooms (each within create()'s per-window rate limit) and
    then call the unthrottled start() on all of them, each firing real
    outbound LLM calls, far past ``max_concurrent_games``."""
    settings_store.set_value(db, "max_concurrent_games", 1, None)
    room_a = room_b = None
    try:
        creator = create_random_user(db)
        room_a = await service.create(creator, _two_agent())
        room_b = await service.create(creator, _two_agent())  # both still "waiting"
        await service.start(room_a.id)  # consumes the one slot

        with pytest.raises(CapacityExceededError):
            await service.start(room_b.id)
        # the refused room must be untouched, not half-started
        assert service.get(room_b.id).status == "waiting"
    finally:
        if room_a is not None:
            await service.stop(room_a.id, "test cleanup")
        settings_store.set_value(
            db, "max_concurrent_games", settings.MAX_CONCURRENT_GAMES, None
        )


# -- mass assignment / seat reservation -------------------------------------


async def test_create_seat_user_id_must_be_the_creators_own(
    db: Session, service: RoomService
) -> None:
    """A seat's ``user_id`` may only ever be the creator seating themself --
    accepting anyone else's id here would force-seat a victim as "filled"
    with no accept/decline step (mass assignment)."""
    creator = create_random_user(db)
    victim = create_random_user(db)
    spec = RoomCreate(
        game="uno_classic",
        seat_count=2,
        visibility="private",
        seats=[
            RoomSeatSpec(kind="human", user_id=victim.id),
            RoomSeatSpec(kind="human"),
        ],
    )
    with pytest.raises(ValueError, match="creator's own id"):
        await service.create(creator, spec)


async def test_create_seat_user_id_as_creators_own_is_accepted(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    spec = RoomCreate(
        game="uno_classic",
        seat_count=2,
        visibility="private",
        seats=[
            RoomSeatSpec(kind="human", user_id=creator.id),
            RoomSeatSpec(kind="human"),
        ],
    )
    room = await service.create(creator, spec)
    seats = service.seats(room.id)
    assert seats[0].user_id == creator.id
    assert seats[0].status == "filled"


async def test_join_fallback_never_steals_a_seat_reserved_for_someone_else(
    db: Session, service: RoomService
) -> None:
    """A seat with ``invited_user_id`` set is reserved: an uninvited joiner
    filling it via the "any open seat" fallback would silently lock the
    intended invitee out."""
    creator = create_random_user(db)
    invitee = create_random_user(db)
    gatecrasher = create_random_user(db)
    spec = RoomCreate(
        game="uno_classic",
        seat_count=2,
        visibility="unlisted",
        seats=[
            RoomSeatSpec(kind="human", invited_user_id=invitee.id),
            RoomSeatSpec(kind="human"),
        ],
    )
    room = await service.create(creator, spec)

    taken = await service.join_by_code(gatecrasher, room.code)
    assert taken.user_id == gatecrasher.id

    remaining = next(s for s in service.seats(room.id) if s.seat != taken.seat)
    assert remaining.invited_user_id == invitee.id
    assert remaining.status == "open"

    # the invitee can still claim their reserved seat afterwards
    invited_seat = await service.join_by_code(invitee, room.code)
    assert invited_seat.user_id == invitee.id
    assert invited_seat.invited_user_id is None


# -- finished status ----------------------------------------------------------


async def test_finish_room_marks_an_in_progress_room_finished(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_agent())
    room.status = "in_progress"
    db.add(room)
    db.commit()

    await service._finish_room(str(room.id), "finished")
    db.expire(room)  # _finish_room commits through its own fresh Session

    updated = service.get(room.id)
    assert updated.status == "finished"
    assert updated.finished_at is not None


async def test_finish_room_does_not_overwrite_an_already_stopped_room(
    db: Session, service: RoomService
) -> None:
    """An explicit ``stop()`` already wrote "stopped"; the completion
    callback racing in afterwards must not clobber it back to "finished"."""
    creator = create_random_user(db)
    room = await service.create(creator, _two_agent())
    room.status = "stopped"
    db.add(room)
    db.commit()

    await service._finish_room(str(room.id), "finished")
    db.expire(room)

    assert service.get(room.id).status == "stopped"


# -- queueing (Phase 16) -------------------------------------------------


async def test_join_by_code_enqueues_when_room_is_full(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    for _ in range(2):
        await service.join_by_code(create_random_user(db), room.code)

    latecomer = create_random_user(db)
    with pytest.raises(RoomFullError):
        await service.join_by_code(latecomer, room.code)
    assert await service.queue.position(f"room:{room.id}", str(latecomer.id)) == 1


async def test_leave_offers_the_freed_seat_and_a_random_joiner_cannot_steal_it(
    db: Session, service: RoomService
) -> None:
    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    occupants = [create_random_user(db) for _ in range(2)]
    seats = [await service.join_by_code(u, room.code) for u in occupants]

    waiter = create_random_user(db)
    with pytest.raises(RoomFullError):
        await service.join_by_code(waiter, room.code)

    await service.leave(occupants[0], room.id)
    freed_seat = seats[0].seat
    reloaded = next(s for s in service.seats(room.id) if s.seat == freed_seat)
    assert reloaded.status == "offered"  # held for the waiter, not generally open
    assert reloaded.user_id is None

    # A fresh joiner (never queued) cannot steal an "offered" seat -- only
    # the waiter's own accept can claim it.
    other = create_random_user(db)
    with pytest.raises(RoomFullError):
        await service.join_by_code(other, room.code)

    target = await service.join_by_code(waiter, room.code)
    assert target.seat == freed_seat
    assert target.user_id == waiter.id
    assert target.status == "filled"


async def test_expired_offer_is_reclaimed_and_reoffered_to_the_next_waiter(
    db: Session,
    runner: GameRunner,
    registry: ModelRegistry,
    bus: InMemoryEventBus,
    rds: Redis,
) -> None:
    """A real, short TTL (not a `now_ms` override -- that's already
    covered at the `JoinQueue` level in `tests/unit/test_join_queue.py`;
    this proves the *RoomService*-level reclaim-and-reoffer end to end)."""
    fast_queue = JoinQueue(rds, offer_ttl_s=1)
    service = RoomService(db, runner, fast_queue, registry, bus)

    creator = create_random_user(db)
    room = await service.create(creator, _two_human())
    a, b = create_random_user(db), create_random_user(db)
    await service.join_by_code(a, room.code)
    await service.join_by_code(b, room.code)

    first_waiter = create_random_user(db)
    second_waiter = create_random_user(db)
    with pytest.raises(RoomFullError):
        await service.join_by_code(first_waiter, room.code)
    with pytest.raises(RoomFullError):
        await service.join_by_code(second_waiter, room.code)

    await service.leave(a, room.id)  # offers a's seat to first_waiter
    await asyncio.sleep(1.2)  # let the 1s offer genuinely expire

    # first_waiter's own retry sweeps first (finds no live offer -- it
    # just expired), reclaiming the seat; since first_waiter is still at
    # the head of the line (this was their only miss so far), it's
    # immediately re-offered and re-accepted within this same call --
    # a self-healing retry, not a second failure.
    target = await service.join_by_code(first_waiter, room.code)
    assert target.user_id == first_waiter.id

    # second_waiter was never touched by any of this -- still queued,
    # now at the head.
    assert await fast_queue.position(f"room:{room.id}", str(second_waiter.id)) == 1


async def test_finish_room_promotes_the_next_globally_queued_room(
    db: Session, service: RoomService, runner: GameRunner
) -> None:
    """A concurrent-game slot freeing (via `_finish_room`) tries to start
    whatever room is at the head of the global queue -- the join-queue
    wiring (Phase 16), not the model-swap scheduler (also Phase 16, but a
    separate concern)."""
    settings_store.set_value(db, "max_concurrent_games", 1, None)
    room_b = None
    try:
        creator = create_random_user(db)
        room_a = await service.create(creator, _two_agent())
        room_b = await service.create(creator, _two_agent())  # both still "waiting"
        # Simulate a running game without a real GameRunner task -- matches
        # `test_finish_room_writes_finished_and_finished_at`'s own pattern
        # above; capacity accounting only ever reads `Room.status` from the
        # DB, so this alone is enough to make room_b's start() see count>=limit.
        # (Must happen *after* both creates: `create()` itself is still
        # gated by the unchanged, hard-refusing `capacity_check()`.)
        room_a.status = "in_progress"
        db.add(room_a)
        db.commit()

        with pytest.raises(CapacityExceededError):
            await service.start(room_b.id)
        assert await service.queue.position("global", str(room_b.id)) == 1

        await service._finish_room(str(room_a.id), "finished")
        db.expire_all()

        # Promotion runs `start()` on room_b for real, from inside
        # `_finish_room`'s own fresh session -- poll rather than assume
        # it's already committed and visible to this session.
        for _ in range(200):
            if service.get(room_b.id).status == "in_progress":
                break
            await asyncio.sleep(0.01)
        assert service.get(room_b.id).status == "in_progress"
        assert runner.get(str(room_b.id)) is not None
        assert await service.queue.position("global", str(room_b.id)) is None
    finally:
        if room_b is not None:
            await service.stop(room_b.id, "test cleanup")
        settings_store.set_value(
            db, "max_concurrent_games", settings.MAX_CONCURRENT_GAMES, None
        )
