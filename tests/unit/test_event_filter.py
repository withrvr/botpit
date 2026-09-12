from __future__ import annotations

import pytest

from botpit.events import (
    PRIVILEGED,
    PUBLIC,
    AgentReasoning,
    EventEnvelope,
    HandUpdated,
    MoveApplied,
    Subscriber,
    Visibility,
    filter_for_subscriber,
    make_event,
    seat,
)

PRIV = Subscriber(kind="privileged")
P1 = Subscriber(kind="player", seat=1)
P2 = Subscriber(kind="player", seat=2)
P_NOSEAT = Subscriber(kind="player")


def _event(visibility: Visibility) -> EventEnvelope:
    return EventEnvelope(room_id="r", type="t", visibility=visibility, payload={})


@pytest.mark.parametrize(
    ("visibility", "subscriber", "expected"),
    [
        (PUBLIC, PRIV, True),
        (PUBLIC, P1, True),
        (PUBLIC, P_NOSEAT, True),
        (seat(1), PRIV, True),
        (seat(1), P1, True),
        (seat(1), P2, False),
        (seat(1), P_NOSEAT, False),
        (PRIVILEGED, PRIV, True),
        (PRIVILEGED, P1, False),
        (PRIVILEGED, P_NOSEAT, False),
    ],
)
def test_visibility_matrix(visibility: Visibility, subscriber: Subscriber, expected: bool) -> None:
    event = _event(visibility)
    result = filter_for_subscriber(event, subscriber)
    assert (result is event) is expected


def test_player_seat_2_never_sees_seat_1_hand_or_reasoning() -> None:
    hand = make_event("r", HandUpdated(seat=1, cards=["AS"]), seat(1))
    reasoning = make_event("r", AgentReasoning(seat=1, model="m", text="thinking"), PRIVILEGED)
    move = make_event("r", MoveApplied(seat=1, move={"card": "AS"}), PUBLIC)
    own_hand = make_event("r", HandUpdated(seat=2, cards=["KD"]), seat(2))
    seen = [e for e in (hand, reasoning, move, own_hand) if filter_for_subscriber(e, P2)]
    assert seen == [move, own_hand]


def test_unknown_visibility_kind_is_dropped_for_players() -> None:
    class Weird:
        kind = "public"

    event = EventEnvelope.model_construct(
        room_id="r", type="t", visibility=Weird(), payload={}, event_id="x", correlation={}
    )
    assert filter_for_subscriber(event, P1) is None
    assert filter_for_subscriber(event, PRIV) is event
