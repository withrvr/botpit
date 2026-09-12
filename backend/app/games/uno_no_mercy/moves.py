"""Move vocabulary for UNO Show 'em No Mercy (see rules.md for the agent-facing text)."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from app.games.base import Move, Seat
from app.games.cards import Colour, UnoCard


class Play(Move):
    kind: Literal["play"] = "play"
    card_index: int
    declared_colour: Colour | None = None
    called_uno: bool = False
    target_seat: Seat | None = None
    card: UnoCard | None = None
    """Informational: the engine fills it in so logs can name the card. When a
    caller supplies it, it must match the card at ``card_index``."""


class Draw(Move):
    kind: Literal["draw"] = "draw"
    count: int | None = None
    """Informational: how many cards this draw takes (the pending penalty, or 1)."""


class Pass(Move):
    kind: Literal["pass"] = "pass"


class ChallengeUno(Move):
    kind: Literal["challenge_uno"] = "challenge_uno"
    target: Seat


NoMercyMove = Play | Draw | Pass | ChallengeUno
MOVE_ADAPTER: TypeAdapter[NoMercyMove] = TypeAdapter(
    Annotated[NoMercyMove, Field(discriminator="kind")]
)
