"""Deterministic referee: drives a game to completion through seat controllers.

A seat is an abstraction. Whether a human websocket, an LLM agent or a test
script fills it, the referee sees the same ``SeatController.decide`` call
returning the same move dict, validates it through the game's ``parse_move``
and ``apply``, and publishes the same events.
"""

from app.referee.referee import Referee, ReplayMismatchError
from app.referee.runner import GameRunner, RunningGame
from app.referee.seats import (
    ControllerError,
    HumanController,
    PolicyController,
    ScriptedController,
    SeatController,
    SeatSpec,
    TurnContext,
    fallback_move,
)

__all__ = [
    "ControllerError",
    "GameRunner",
    "HumanController",
    "PolicyController",
    "Referee",
    "ReplayMismatchError",
    "RunningGame",
    "ScriptedController",
    "SeatController",
    "SeatSpec",
    "TurnContext",
    "fallback_move",
]
