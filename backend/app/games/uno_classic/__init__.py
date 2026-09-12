"""Classic UNO (108-card deck, official Mattel rules)."""

from __future__ import annotations

from app.games.uno_classic.game import GAME, UnoClassic
from app.games.uno_classic.moves import UnoMove
from app.games.uno_classic.state import UnoState

__all__ = ["GAME", "UnoClassic", "UnoMove", "UnoState"]
