"""Leaderboard aggregation (Phase 12, DECISIONS.md §11): "leaderboard per
human account and per model, with aggregate admin views."

A finished game's ``GameRecord.winner_seat`` names a seat, not a player --
``RoomSeat`` is what ties that seat back to the account or model that
actually played it. No separate snapshot table is needed to do that
durably: ``RoomService.start()`` refuses a room that isn't ``"waiting"``,
and nothing ever puts a room back into ``"waiting"`` after that, so a room
plays exactly one game for its entire lifetime -- ``room_seat`` already *is*
that one game's durable participant record, immutable from the moment
``start()`` runs (``join``/``force_start`` are likewise gated on
``"waiting"``).

Only ``outcome == "finished"`` games count: a ``"stopped"`` game's
``winner_seat`` is always ``None`` (docs/games.md), so it has no result to
credit a win or loss for. A *finished* game's ``winner_seat`` can be
``None`` too (``Referee.run()`` only credits a winner when the top-ranked
seat's hand is empty, so a game whose ranking is score-based rather than
hand-emptying can finish with no seat meeting that check) -- such a game
still counts toward ``games_played`` for every seat in it, just credits no
one a win, which is the right leaderboard behavior for "played, no
determinable winner" rather than a bug to work around here.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func
from sqlmodel import Session, col, select

from app.models import GameRecord, LeaderboardEntry, RoomSeat, User


def _wins() -> Any:
    return func.sum(
        case((col(GameRecord.winner_seat) == col(RoomSeat.seat), 1), else_=0)
    )


def leaderboard_by_account(
    session: Session, game: str | None = None
) -> list[LeaderboardEntry]:
    """One row per human account with at least one finished game."""
    # `func.coalesce(...)` folds `full_name`/`email` into one selected column
    # rather than two -- not just tidier, `select()`'s typed overloads only
    # go up to four positional columns, and a fifth (`email` as its own
    # column, alongside user_id/display_name/games_played/wins) has no
    # matching overload.
    display_name = func.coalesce(User.full_name, User.email)
    stmt = (
        select(
            RoomSeat.user_id,
            display_name.label("display_name"),
            func.count().label("games_played"),
            _wins().label("wins"),
        )
        .join(RoomSeat, col(RoomSeat.room_id) == col(GameRecord.room_id))
        .join(User, col(User.id) == col(RoomSeat.user_id))
        .where(GameRecord.outcome == "finished", RoomSeat.kind == "human")
        .group_by(col(RoomSeat.user_id), col(User.full_name), col(User.email))
        .order_by(_wins().desc(), func.count().desc())
    )
    if game is not None:
        stmt = stmt.where(GameRecord.game == game)
    return [
        LeaderboardEntry(
            subject_id=str(user_id),
            display_name=name,
            games_played=games_played,
            wins=wins,
            win_rate=wins / games_played,
        )
        for user_id, name, games_played, wins in session.exec(stmt).all()
    ]


def leaderboard_by_model(
    session: Session, game: str | None = None
) -> list[LeaderboardEntry]:
    """One row per model name with at least one finished game."""
    stmt = (
        select(
            RoomSeat.model,
            func.count().label("games_played"),
            _wins().label("wins"),
        )
        .join(RoomSeat, col(RoomSeat.room_id) == col(GameRecord.room_id))
        .where(GameRecord.outcome == "finished", RoomSeat.kind == "agent")
        .group_by(RoomSeat.model)
        .order_by(_wins().desc(), func.count().desc())
    )
    if game is not None:
        stmt = stmt.where(GameRecord.game == game)
    entries = []
    for model, games_played, wins in session.exec(stmt).all():
        # `RoomSeat.model` is nullable at the schema level, but a kind="agent"
        # row is never written without one (`RoomService.create`/
        # `force_start` both always resolve a default before insert) --
        # asserted, not silently coerced, so a real future violation of that
        # invariant fails loudly here rather than mislabeling a row "None".
        assert model is not None
        entries.append(
            LeaderboardEntry(
                subject_id=model,
                display_name=model,
                games_played=games_played,
                wins=wins,
                win_rate=wins / games_played,
            )
        )
    return entries
