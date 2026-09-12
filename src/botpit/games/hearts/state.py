"""Hearts state. ``hands`` are the live hands; pending pass picks are indices
held separately so ``player_view`` never has to reveal them."""

from __future__ import annotations

from typing import Literal

from botpit.games.base import GameState, Seat
from botpit.games.cards import FrenchCard


class HeartsState(GameState):
    hands: tuple[tuple[FrenchCard, ...], ...]
    phase: Literal["pass", "play"] = "pass"
    pending_passes: tuple[tuple[int, int, int] | None, ...]
    leader: Seat = 0
    trick: tuple[FrenchCard, ...] = ()  # card i was played by seat (leader + i) % seat_count
    hearts_broken: bool = False
    taken: tuple[tuple[FrenchCard, ...], ...]  # cards won per seat
