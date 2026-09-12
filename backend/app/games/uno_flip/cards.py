"""UNO Flip! double-faced cards and the 112-card deck."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.games.base import Frozen
from app.games.cards import DARK_COLOURS, LIGHT_COLOURS, Colour, UnoCard, shuffled

if TYPE_CHECKING:
    import random

Side = Literal["light", "dark"]

LIGHT_ACTIONS = ("draw1", "reverse", "skip", "flip")
DARK_ACTIONS = ("draw5", "reverse", "skip_all", "flip")
LIGHT_WILDS = ("wild", "wild_draw2")
DARK_WILDS = ("wild", "wild_draw_colour")

POINTS = {
    "draw1": 10,
    "draw5": 20,
    "reverse": 20,
    "skip": 20,
    "flip": 20,
    "skip_all": 30,
    "wild": 40,
    "wild_draw2": 50,
    "wild_draw_colour": 60,
}


class FlipCard(Frozen):
    """One physical card: a light face and a dark face. Only the side the game
    is on is visible; the other face travels hidden with the card."""

    light: UnoCard
    dark: UnoCard

    def face(self, side: Side) -> UnoCard:
        return self.light if side == "light" else self.dark


def points(face: UnoCard) -> int:
    return int(face.rank) if face.is_number else POINTS[face.rank]


def _faces(
    colours: tuple[Colour, ...], actions: tuple[str, ...], wilds: tuple[str, ...]
) -> list[UnoCard]:
    faces = [
        UnoCard(colour=c, rank=r)
        for c in colours
        for r in [*map(str, range(1, 10)), *actions]
        for _ in range(2)
    ]
    faces += [UnoCard(colour=None, rank=w) for w in wilds for _ in range(4)]
    return faces


def light_faces() -> list[UnoCard]:
    return _faces(LIGHT_COLOURS, LIGHT_ACTIONS, LIGHT_WILDS)


def dark_faces() -> list[UnoCard]:
    return _faces(DARK_COLOURS, DARK_ACTIONS, DARK_WILDS)


def build_deck(rng: random.Random) -> tuple[FlipCard, ...]:
    """112 cards. Mattel prints a fixed light/dark pairing; we pair the two face
    lists by shuffling each once (documented deviation, identical per-side
    distribution). The zip of two shuffled lists is itself a shuffled deck."""
    light = shuffled(rng, light_faces())
    dark = shuffled(rng, dark_faces())
    return tuple(
        FlipCard(light=lf, dark=df) for lf, df in zip(light, dark, strict=True)
    )
