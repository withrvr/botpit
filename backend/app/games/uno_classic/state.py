from __future__ import annotations

from typing import Literal

from app.games.base import GameState, Seat
from app.games.cards import Colour, UnoCard

Phase = Literal["play", "after_draw", "draw4_response"]


class UnoState(GameState):
    hands: tuple[tuple[UnoCard, ...], ...]
    draw_pile: tuple[UnoCard, ...]
    """Last element is the top (next card drawn)."""
    discard_pile: tuple[UnoCard, ...]
    """Last element is the top (the card being matched)."""
    active_colour: Colour | None
    """``None`` only while a first-discard Wild awaits the first play: any card matches."""
    direction: Literal[1, -1] = 1
    turn: Seat = 0
    phase: Phase = "play"
    """``after_draw``: ``turn`` just drew and the drawn card is the last in its hand.
    ``draw4_response``: ``turn`` must accept or challenge the Wild Draw Four."""
    uno_called: tuple[bool, ...]
    """False for a seat only while it holds one card, did not call UNO, and the
    catch window is still open. Closed windows are stored as True."""
    draw4_player: Seat | None = None
    draw4_prev_colour: Colour | None = None
    """Active colour before the pending Wild Draw Four, used to judge a challenge."""
    terminal: bool = False
    winner: Seat | None = None
    """``None`` at a no-progress ending (nobody could draw or play)."""

    @property
    def top(self) -> UnoCard:
        return self.discard_pile[-1]
