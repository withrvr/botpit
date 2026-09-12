"""Event envelope, visibility and the typed payloads every room stream carries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- visibility -------------------------------------------------------------


class PublicVisibility(_Frozen):
    kind: Literal["public"] = "public"


class SeatVisibility(_Frozen):
    kind: Literal["seat"] = "seat"
    seat: int = Field(ge=0)


class PrivilegedVisibility(_Frozen):
    kind: Literal["privileged"] = "privileged"


Visibility = Annotated[
    PublicVisibility | SeatVisibility | PrivilegedVisibility, Field(discriminator="kind")
]

PUBLIC = PublicVisibility()
PRIVILEGED = PrivilegedVisibility()


def seat(n: int) -> SeatVisibility:
    return SeatVisibility(seat=n)


# --- envelope ---------------------------------------------------------------


class EventEnvelope(_Frozen):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    room_id: str
    seq: int | None = None
    """Per-room monotonic counter, assigned by the bus at publish."""
    type: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    visibility: Visibility
    payload: dict[str, Any]
    correlation: dict[str, str] = Field(default_factory=dict)
    stream_id: str | None = None
    """Bus-assigned position (Redis entry id or ``"<seq>-0"``); pass back as ``since``."""


# --- payloads ---------------------------------------------------------------


class RoomCreated(_Frozen):
    game: str
    seats: int


class SeatFilled(_Frozen):
    seat: int
    player: str


class GameStarted(_Frozen):
    pass


class TurnStarted(_Frozen):
    seat: int


class MoveApplied(_Frozen):
    seat: int
    move: dict[str, Any]


class HandUpdated(_Frozen):
    """Publish with ``seat(n)`` visibility."""

    seat: int
    cards: list[Any]


class AgentReasoning(_Frozen):
    """Publish with ``PRIVILEGED`` visibility."""

    seat: int
    model: str
    text: str


class AgentTurnMetrics(_Frozen):
    seat: int
    model: str
    latency_ms: int
    ok: bool


class ChatMessage(_Frozen):
    seat: int
    text: str


class GameFinished(_Frozen):
    winner_seat: int | None
    ranking: list[int]


class RoomStopped(_Frozen):
    reason: str


EVENT_TYPES: dict[str, type[BaseModel]] = {
    "room.created": RoomCreated,
    "room.seat_filled": SeatFilled,
    "room.stopped": RoomStopped,
    "game.started": GameStarted,
    "game.turn_started": TurnStarted,
    "game.move_applied": MoveApplied,
    "game.hand_updated": HandUpdated,
    "game.finished": GameFinished,
    "agent.reasoning": AgentReasoning,
    "agent.turn_metrics": AgentTurnMetrics,
    "chat.message": ChatMessage,
}
_TYPE_OF: dict[type[BaseModel], str] = {model: name for name, model in EVENT_TYPES.items()}


def make_event(
    room_id: str,
    payload: BaseModel,
    visibility: Visibility,
    correlation: dict[str, str] | None = None,
) -> EventEnvelope:
    """Wrap a registered payload; ``type`` comes from ``EVENT_TYPES``."""
    try:
        type_ = _TYPE_OF[type(payload)]
    except KeyError:
        msg = f"{type(payload).__name__} is not a registered event payload"
        raise TypeError(msg) from None
    return EventEnvelope(
        room_id=room_id,
        type=type_,
        visibility=visibility,
        payload=payload.model_dump(mode="json"),
        correlation=correlation or {},
    )
