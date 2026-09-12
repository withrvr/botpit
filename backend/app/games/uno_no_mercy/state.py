from typing import Literal

from app.games.base import GameState, Seat
from app.games.cards import Colour, UnoCard


class NoMercyState(GameState):
    hands: tuple[tuple[UnoCard, ...], ...]
    draw_pile: tuple[UnoCard, ...]
    """Index 0 is the top."""
    discard_pile: tuple[UnoCard, ...]
    """Index -1 is the top."""
    active_colour: Colour
    direction: Literal[1, -1] = 1
    turn: Seat = 0
    pending_draw: int = 0
    after_draw: bool = False
    uno_pending: Seat | None = None
    """Seat that went down to one card without calling UNO; challengeable until the
    next seat acts."""
    winner: Seat | None = None
    stalled: bool = False
    """The seat to move could neither play nor draw: the game ends by ranking."""

    @property
    def top(self) -> UnoCard:
        return self.discard_pile[-1]

    @property
    def phase(self) -> str:
        if self.pending_draw:
            return "draw_response"
        return "after_draw" if self.after_draw else "play"

    @property
    def active_seats(self) -> list[Seat]:
        return [s for s in range(self.seat_count) if s not in self.eliminated]
