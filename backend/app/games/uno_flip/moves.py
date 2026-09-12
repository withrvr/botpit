from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from app.games.base import Move, Seat
from app.games.cards import Colour, UnoCard


class Play(Move):
    kind: Literal["play"] = "play"
    card_index: int
    declared_colour: Colour | None = (
        None  # required for wild faces, forbidden otherwise
    )
    called_uno: bool = False  # only when this play leaves you with one card
    card: UnoCard | None = None  # optional self-check: the active face you mean to play


class Draw(Move):
    kind: Literal["draw"] = "draw"


class Pass(Move):
    kind: Literal["pass"] = "pass"


class ChallengeUno(Move):
    kind: Literal["challenge_uno"] = "challenge_uno"
    target: Seat


class ChallengeWildDraw(Move):
    kind: Literal["challenge_wild_draw"] = "challenge_wild_draw"


class AcceptWildDraw(Move):
    kind: Literal["accept_wild_draw"] = "accept_wild_draw"


FlipMove = Annotated[
    Play | Draw | Pass | ChallengeUno | ChallengeWildDraw | AcceptWildDraw,
    Field(discriminator="kind"),
]

MOVE_ADAPTER: TypeAdapter[FlipMove] = TypeAdapter(FlipMove)
