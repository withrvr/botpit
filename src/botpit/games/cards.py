"""Card types and helpers shared by more than one game."""

from __future__ import annotations

import random
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from botpit.games.base import Frozen

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from botpit.games.base import GameState


class Colour(StrEnum):
    # Classic / No Mercy / Flip light side
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    # Flip dark side
    PINK = "pink"
    TEAL = "teal"
    ORANGE = "orange"
    PURPLE = "purple"


LIGHT_COLOURS: tuple[Colour, ...] = (Colour.RED, Colour.YELLOW, Colour.GREEN, Colour.BLUE)
DARK_COLOURS: tuple[Colour, ...] = (Colour.PINK, Colour.TEAL, Colour.ORANGE, Colour.PURPLE)


class UnoCard(Frozen):
    """One face of an UNO-family card. ``colour`` is ``None`` for wilds.

    ``rank`` is a lower-case token: ``"0"``-``"9"`` or an action name such as
    ``"skip"``, ``"reverse"``, ``"draw2"``, ``"wild"``, ``"wild_draw4"``.
    Each game documents its own rank vocabulary in its ``rules.md``.
    """

    colour: Colour | None
    rank: str

    @property
    def is_wild(self) -> bool:
        return self.colour is None

    @property
    def is_number(self) -> bool:
        return self.rank.isdigit()

    def label(self) -> str:
        return self.rank if self.colour is None else f"{self.colour} {self.rank}"


class Suit(StrEnum):
    CLUBS = "clubs"
    DIAMONDS = "diamonds"
    HEARTS = "hearts"
    SPADES = "spades"


Rank = Literal[2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]  # 11=J 12=Q 13=K 14=A
RANK_NAMES = {11: "J", 12: "Q", 13: "K", 14: "A"}


class FrenchCard(Frozen):
    suit: Suit
    rank: Rank

    def label(self) -> str:
        return f"{RANK_NAMES.get(self.rank, str(self.rank))}{self.suit[0].upper()}"


def standard_52() -> tuple[FrenchCard, ...]:
    return tuple(FrenchCard(suit=s, rank=r) for s in Suit for r in range(2, 15))


def rng_for(state: GameState) -> random.Random:
    """Deterministic RNG for this exact point of the game.

    Seeded from ``(seed, move_count)`` so any reshuffle is reproducible from the
    event log without storing generator state. Each call site must use it for
    a single logical shuffle per move.
    """
    return random.Random(f"{state.seed}:{state.move_count}")


def rng_for_seed(seed: int) -> random.Random:
    return random.Random(f"{seed}:new")


def shuffled[T](rng: random.Random, cards: Iterable[T]) -> tuple[T, ...]:
    pile = list(cards)
    rng.shuffle(pile)
    return tuple(pile)


def deal[T](
    deck: Sequence[T], seat_count: int, per_seat: int
) -> tuple[tuple[tuple[T, ...], ...], tuple[T, ...]]:
    """Deal ``per_seat`` cards round-robin from the top of ``deck``; return (hands, rest)."""
    hands = tuple(tuple(deck[i::seat_count][:per_seat]) for i in range(seat_count))
    return hands, tuple(deck[seat_count * per_seat :])
