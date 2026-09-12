from typing import Literal

from app.games.base import GameState, Seat
from app.games.cards import Colour
from app.games.uno_flip.cards import FlipCard, Side

Phase = Literal["play", "after_draw", "wild_draw_response"]


class FlipState(GameState):
    hands: tuple[tuple[FlipCard, ...], ...]
    draw_pile: tuple[FlipCard, ...]  # last element is the top
    discard: tuple[FlipCard, ...]  # last element is the top
    side: Side
    turn: Seat
    direction: Literal[1, -1]
    active_colour: (
        Colour | None
    )  # None = open (a wild face is showing with no declaration)
    phase: Phase = "play"
    # While a Wild Draw Two / Wild Draw Colour awaits accept/challenge: who played it
    # and the colour that was active when they did (what a challenge checks against).
    pending_from: Seat | None = None
    pending_prev_colour: Colour | None = None
    uno_pending: Seat | None = None  # played down to one card without calling UNO
    winner: Seat | None = None
    terminal: bool = False
