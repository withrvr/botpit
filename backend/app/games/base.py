"""The game module contract.

A game is a pure, deterministic state machine. Nothing here knows about
seats being human or AI, about models, or about the network: the referee
(Phase 3) drives it, the prompt builder and the API both read it through
``player_view``.

Rules every implementation follows (see docs/engine.md for the reasoning):

* Exactly one ``current_seat`` at a time; ``None`` iff the game is terminal.
* ``apply`` is pure: it returns a new state and never mutates its input.
  An unacceptable move raises ``IllegalMoveError`` with a human-readable reason.
* ``legal_moves(state, current_seat)`` is exactly the set ``apply`` accepts
  for that seat and is non-empty unless the game is terminal.
* Randomness comes only from ``cards.rng_for(state)``: seeded by
  ``(seed, move_count)``, so replaying the same moves gives the same states.
* ``player_view`` is the privacy gate (DECISIONS.md §9): it is the only way
  a seat's information leaves the state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

Seat = int


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Move(Frozen):
    """Base for every move. Subclasses set ``kind: Literal["..."]``."""

    kind: str
    seat: Seat


class IllegalMoveError(Exception):
    def __init__(self, move: Move, reason: str) -> None:
        super().__init__(f"{move.kind} by seat {move.seat}: {reason}")
        self.move = move
        self.reason = reason


class GameState(Frozen):
    """Common shape of every game's state. Games extend it with their own fields."""

    slug: str
    seed: int
    seat_count: int
    move_count: int = 0
    hands: tuple[tuple[Any, ...], ...]
    scores: tuple[int, ...]
    eliminated: tuple[Seat, ...] = ()


class PlayerView(Frozen):
    """Everything one seat is allowed to know. Serialised as-is to agents and browsers."""

    slug: str
    seat: Seat
    phase: str
    current_seat: Seat | None
    direction: Literal[1, -1]
    hand: list[dict[str, Any]]
    hand_counts: list[int]
    table: dict[str, Any] = Field(default_factory=dict)
    legal_moves: list[dict[str, Any]]
    scores: list[int]
    eliminated: list[Seat]
    terminal: bool


class Game[S: GameState, M: Move](ABC):
    slug: ClassVar[str]
    title: ClassVar[str]
    min_seats: ClassVar[int]
    max_seats: ClassVar[int]

    @abstractmethod
    def new_game(self, seed: int, seat_count: int) -> S: ...

    @abstractmethod
    def parse_move(self, data: dict[str, Any]) -> M:
        """Validate an untrusted dict (websocket or model output) into a move."""

    @abstractmethod
    def current_seat(self, state: S) -> Seat | None: ...

    @abstractmethod
    def legal_moves(self, state: S, seat: Seat) -> list[M]: ...

    @abstractmethod
    def apply(self, state: S, move: M) -> S: ...

    @abstractmethod
    def ranking(self, state: S) -> list[Seat]:
        """Best first; a permutation of ``range(seat_count)``. Defined at every state."""

    @abstractmethod
    def player_view(self, state: S, seat: Seat) -> PlayerView: ...

    @abstractmethod
    def rules_markdown(self) -> str: ...

    @abstractmethod
    def describe_move(self, move: M) -> str: ...

    def is_terminal(self, state: S) -> bool:
        return self.current_seat(state) is None

    def hand_view(self, state: S, seat: Seat) -> list[dict[str, Any]]:
        """What ``player_view(state, seat).hand`` must equal. Override when a
        card has hidden parts even for its holder (UNO Flip shows one face)."""
        return [
            c.model_dump(mode="json") if hasattr(c, "model_dump") else c
            for c in state.hands[seat]
        ]

    def check_seat_count(self, seat_count: int) -> None:
        if not self.min_seats <= seat_count <= self.max_seats:
            msg = f"{self.slug} takes {self.min_seats}-{self.max_seats} seats, not {seat_count}"
            raise ValueError(msg)


AnyGame = Game[Any, Any]
