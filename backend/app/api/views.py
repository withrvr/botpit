"""The one serialiser the HTTP/websocket layer uses for a human's own seat.

One call site on purpose: ``player_view`` is the privacy gate (DECISIONS.md §9).
"""

from __future__ import annotations

from typing import Any

from app.games.base import AnyGame, GameState, Seat


def view_payload(game: AnyGame, state: GameState, seat: Seat) -> dict[str, Any]:
    return game.player_view(state, seat).model_dump(mode="json")


__all__ = ["view_payload"]
