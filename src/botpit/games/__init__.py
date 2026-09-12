"""Game catalog. Add a game by dropping a package under ``botpit/games/`` and
listing it in ``_GAMES``; nothing outside the package changes."""

from __future__ import annotations

from botpit.games.base import AnyGame, Game, GameState, IllegalMoveError, Move, PlayerView, Seat
from botpit.games.uno_no_mercy import GAME as _uno_no_mercy  # noqa: N811

_GAMES: tuple[AnyGame, ...] = (_uno_no_mercy,)

GAME_CATALOG: dict[str, AnyGame] = {g.slug: g for g in _GAMES}


def get_game(slug: str) -> AnyGame:
    try:
        return GAME_CATALOG[slug]
    except KeyError:
        msg = f"unknown game {slug!r}; known: {sorted(GAME_CATALOG)}"
        raise KeyError(msg) from None


__all__ = [
    "GAME_CATALOG",
    "AnyGame",
    "Game",
    "GameState",
    "IllegalMoveError",
    "Move",
    "PlayerView",
    "Seat",
    "get_game",
]
