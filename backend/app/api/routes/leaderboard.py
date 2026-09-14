"""Aggregate leaderboards (Phase 12, DECISIONS.md §11): "leaderboard per
human account and per model, with aggregate admin views." Granted to every
default role (``admin``/``player``/``spectator``, see
``app/authz/permissions.py``) -- a leaderboard is meant to be seen, not an
admin-only report; "aggregate admin views" (docs/admin-and-observability.md)
just means the same data also surfaces in the admin dashboard, not a
separate, more-restricted endpoint."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import SessionDep
from app.authz import P, require_permission
from app.leaderboard import leaderboard_by_account, leaderboard_by_model
from app.models import LeaderboardEntry

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

_view = Depends(require_permission(P.LEADERBOARD_VIEW))
GameFilter = Annotated[str | None, Query(max_length=64)]


@router.get("/accounts", response_model=list[LeaderboardEntry], dependencies=[_view])
def accounts_leaderboard(session: SessionDep, game: GameFilter = None) -> Any:
    return leaderboard_by_account(session, game)


@router.get("/models", response_model=list[LeaderboardEntry], dependencies=[_view])
def models_leaderboard(session: SessionDep, game: GameFilter = None) -> Any:
    return leaderboard_by_model(session, game)
