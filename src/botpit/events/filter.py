"""Per-subscriber visibility filter. This is a security boundary: allow-list only."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from botpit.events.schema import EventEnvelope, PublicVisibility, SeatVisibility


class Subscriber(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["privileged", "player"]
    seat: int | None = Field(default=None, ge=0)


def filter_for_subscriber(event: EventEnvelope, subscriber: Subscriber) -> EventEnvelope | None:
    """Return ``event`` if ``subscriber`` may see it, else ``None``.

    Privileged sees everything. Anyone else sees public events and events
    addressed to their own seat. Anything unrecognised is dropped.
    """
    if subscriber.kind == "privileged":
        return event
    vis = event.visibility
    if isinstance(vis, PublicVisibility):
        return event
    if isinstance(vis, SeatVisibility):
        return event if subscriber.seat is not None and vis.seat == subscriber.seat else None
    return None
