from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from botpit.games.base import Move, Seat
from botpit.games.cards import Colour, UnoCard


class Play(Move):
    kind: Literal["play"] = "play"
    card_index: int
    declared_colour: Colour | None = None
    """Required for wilds, must be omitted for coloured cards."""
    called_uno: bool = False
    """Only allowed on the play that leaves you exactly one card."""
    card: UnoCard | None = None
    """Optional echo of the card at ``card_index``; filled in by ``legal_moves`` so
    logs can name the card. Rejected if it disagrees with the hand."""


class Draw(Move):
    kind: Literal["draw"] = "draw"


class Pass(Move):
    kind: Literal["pass"] = "pass"


class ChallengeUno(Move):
    kind: Literal["challenge_uno"] = "challenge_uno"
    target: Seat


class ChallengeDraw4(Move):
    kind: Literal["challenge_draw4"] = "challenge_draw4"


class AcceptDraw4(Move):
    kind: Literal["accept_draw4"] = "accept_draw4"


UnoMove = Annotated[
    Play | Draw | Pass | ChallengeUno | ChallengeDraw4 | AcceptDraw4,
    Field(discriminator="kind"),
]

MOVE_ADAPTER: TypeAdapter[UnoMove] = TypeAdapter(UnoMove)
