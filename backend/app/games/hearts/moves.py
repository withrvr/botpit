"""Hearts move vocabulary: ``pass_cards`` during the pass, ``play`` afterwards."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator

from app.games.base import Move
from app.games.cards import FrenchCard


class PassCards(Move):
    kind: Literal["pass_cards"] = "pass_cards"
    cards: tuple[int, int, int]  # indices into own hand; stored sorted

    @field_validator("cards")
    @classmethod
    def _sort(cls, value: tuple[int, int, int]) -> tuple[int, int, int]:
        a, b, c = sorted(value)
        return (a, b, c)


class Play(Move):
    kind: Literal["play"] = "play"
    card_index: int
    card: FrenchCard | None = (
        None  # filled in by legal_moves; checked against card_index if sent
    )


HeartsMove = Annotated[PassCards | Play, Field(discriminator="kind")]
MOVE_ADAPTER: TypeAdapter[HeartsMove] = TypeAdapter(HeartsMove)
