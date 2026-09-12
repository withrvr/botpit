"""UNO Flip! rules engine. See rules.md for the player-facing rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from botpit.games.base import Game, IllegalMoveError, PlayerView, Seat
from botpit.games.cards import (
    DARK_COLOURS,
    LIGHT_COLOURS,
    Colour,
    deal,
    rng_for,
    rng_for_seed,
    shuffled,
)
from botpit.games.uno_flip.cards import FlipCard, Side, build_deck, points
from botpit.games.uno_flip.moves import (
    MOVE_ADAPTER,
    AcceptWildDraw,
    ChallengeUno,
    ChallengeWildDraw,
    Draw,
    FlipMove,
    Pass,
    Play,
)
from botpit.games.uno_flip.state import FlipState, Phase

if TYPE_CHECKING:
    import random

    from botpit.games.cards import UnoCard

RULES = (Path(__file__).parent / "rules.md").read_text(encoding="utf-8")
HAND_SIZE = 7
WILD_DRAWS = ("wild_draw2", "wild_draw_colour")


def colours_for(side: Side) -> tuple[Colour, ...]:
    return LIGHT_COLOURS if side == "light" else DARK_COLOURS


def playable(face: UnoCard, top: UnoCard, colour: Colour | None) -> bool:
    return face.is_wild or colour is None or face.colour == colour or face.rank == top.rank


def hand_points(hand: tuple[FlipCard, ...], side: Side) -> int:
    return sum(points(c.face(side)) for c in hand)


@dataclass
class _Table:
    """Mutable scratch copy of a state for one move; ``to_state`` freezes it back."""

    seed: int
    n: int
    rng: random.Random
    hands: list[list[FlipCard]]
    draw_pile: list[FlipCard]
    discard: list[FlipCard]
    side: Side
    turn: Seat
    direction: Literal[1, -1]
    colour: Colour | None
    phase: Phase = "play"
    pending_from: Seat | None = None
    pending_prev_colour: Colour | None = None
    uno_pending: Seat | None = None

    @classmethod
    def of(cls, state: FlipState) -> _Table:
        return cls(
            seed=state.seed,
            n=state.seat_count,
            rng=rng_for(state),
            hands=[list(h) for h in state.hands],
            draw_pile=list(state.draw_pile),
            discard=list(state.discard),
            side=state.side,
            turn=state.turn,
            direction=state.direction,
            colour=state.active_colour,
            phase=state.phase,
            pending_from=state.pending_from,
            pending_prev_colour=state.pending_prev_colour,
            uno_pending=state.uno_pending,
        )

    def to_state(self, move_count: int) -> FlipState:
        hands = tuple(tuple(h) for h in self.hands)
        winner = next((s for s, h in enumerate(hands) if not h), None)
        stuck = (
            self.phase == "play"
            and self.available() == 0
            and not self.playable_indices(hands[self.turn])
        )
        terminal = winner is not None or stuck
        return FlipState(
            slug=GAME.slug,
            seed=self.seed,
            seat_count=self.n,
            move_count=move_count,
            hands=hands,
            scores=tuple(hand_points(h, self.side) if terminal else 0 for h in hands),
            draw_pile=tuple(self.draw_pile),
            discard=tuple(self.discard),
            side=self.side,
            turn=self.turn,
            direction=self.direction,
            active_colour=self.colour,
            phase=self.phase,
            pending_from=self.pending_from,
            pending_prev_colour=self.pending_prev_colour,
            uno_pending=self.uno_pending,
            winner=winner,
            terminal=terminal,
        )

    def face(self, card: FlipCard) -> UnoCard:
        return card.face(self.side)

    def top(self) -> UnoCard:
        return self.face(self.discard[-1])

    def available(self) -> int:
        return len(self.draw_pile) + len(self.discard) - 1

    def playable_indices(self, hand: list[FlipCard] | tuple[FlipCard, ...]) -> list[int]:
        if self.phase == "wild_draw_response":
            return []
        indices = range(len(hand)) if self.phase == "play" else [len(hand) - 1]
        top = self.top()
        return [i for i in indices if playable(self.face(hand[i]), top, self.colour)]

    def next_seat(self, seat: Seat, steps: int = 1) -> Seat:
        return (seat + self.direction * steps) % self.n

    def draw_one(self) -> FlipCard | None:
        if not self.draw_pile:
            if len(self.discard) <= 1:
                return None
            self.draw_pile = list(shuffled(self.rng, self.discard[:-1]))
            self.discard = self.discard[-1:]
        return self.draw_pile.pop()

    def give(self, seat: Seat, count: int) -> None:
        for _ in range(count):
            card = self.draw_one()
            if card is None:
                return
            self.hands[seat].append(card)

    def give_until(self, seat: Seat, colour: Colour) -> None:
        # Bounded by the cards that exist; if the colour never shows, stop.
        while (card := self.draw_one()) is not None:
            self.hands[seat].append(card)
            if self.face(card).colour == colour:
                return

    def flip(self) -> None:
        self.side = "dark" if self.side == "light" else "light"
        self.discard.reverse()
        self.colour = self.top().colour

    def has_colour(self, seat: Seat, colour: Colour | None) -> bool:
        return colour is not None and any(self.face(c).colour == colour for c in self.hands[seat])


class UnoFlip(Game[FlipState, FlipMove]):
    slug = "uno_flip"
    title = "UNO Flip!"
    min_seats = 2
    max_seats = 10

    def new_game(self, seed: int, seat_count: int) -> FlipState:
        self.check_seat_count(seat_count)
        rng = rng_for_seed(seed)
        hands, rest = deal(build_deck(rng), seat_count, HAND_SIZE)
        pile = list(rest)
        first = pile.pop()
        while first.light.rank == "wild_draw2":  # returned to the deck and reshuffled
            pile.append(first)
            rng.shuffle(pile)
            first = pile.pop()
        t = _Table(
            seed=seed,
            n=seat_count,
            rng=rng,
            hands=[list(h) for h in hands],
            draw_pile=pile,
            discard=[first],
            side="light",
            turn=0,
            direction=1,
            colour=first.light.colour,
        )
        match first.light.rank:
            case "draw1":
                t.give(0, 1)
                t.turn = 1
            case "skip":
                t.turn = 1
            case "reverse":
                t.direction = -1
                t.turn = t.next_seat(0)
            case "flip":
                t.flip()
        return t.to_state(0)

    def parse_move(self, data: dict[str, Any]) -> FlipMove:
        return MOVE_ADAPTER.validate_python(data)

    def current_seat(self, state: FlipState) -> Seat | None:
        return None if state.terminal else state.turn

    def legal_moves(self, state: FlipState, seat: Seat) -> list[FlipMove]:
        if state.terminal or seat != state.turn:
            return []
        t = _Table.of(state)
        moves: list[FlipMove] = []
        if t.uno_pending is not None and t.uno_pending != seat:
            moves.append(ChallengeUno(seat=seat, target=t.uno_pending))
        if t.phase == "wild_draw_response":
            return [*moves, AcceptWildDraw(seat=seat), ChallengeWildDraw(seat=seat)]
        hand = state.hands[seat]
        playable_now = t.playable_indices(hand)
        for i in playable_now:
            face = t.face(hand[i])
            colours = colours_for(t.side) if face.is_wild else (None,)
            unos = (False, True) if len(hand) == 2 else (False,)
            moves += [
                Play(seat=seat, card_index=i, declared_colour=c, called_uno=u, card=face)
                for c in colours
                for u in unos
            ]
        if t.phase == "after_draw":
            moves.append(Pass(seat=seat))
        elif not playable_now and t.available() > 0:
            moves.append(Draw(seat=seat))
        return moves

    def apply(self, state: FlipState, move: FlipMove) -> FlipState:
        if state.terminal:
            raise IllegalMoveError(move, "the game is over")
        if move.seat != state.turn:
            raise IllegalMoveError(move, "not your turn")
        t = _Table.of(state)
        if isinstance(move, ChallengeUno):
            self._challenge_uno(t, move)
        else:
            t.uno_pending = None  # the window closes as soon as the next seat acts
            match move:
                case Play():
                    self._play(t, move)
                case Draw():
                    self._draw(t, move)
                case Pass():
                    self._pass(t, move)
                case AcceptWildDraw():
                    self._respond(t, move, challenge=False)
                case ChallengeWildDraw():
                    self._respond(t, move, challenge=True)
        return t.to_state(state.move_count + 1)

    def _challenge_uno(self, t: _Table, move: ChallengeUno) -> None:
        if t.uno_pending is None or move.target != t.uno_pending:
            raise IllegalMoveError(move, f"seat {move.target} has not missed an UNO call")
        if move.target == move.seat:
            raise IllegalMoveError(move, "you cannot catch yourself")
        t.give(move.target, 2)
        t.uno_pending = None

    def _play(self, t: _Table, move: Play) -> None:
        seat, hand = move.seat, t.hands[move.seat]
        if t.phase == "wild_draw_response":
            raise IllegalMoveError(move, "respond to the wild draw first (accept or challenge)")
        if not 0 <= move.card_index < len(hand):
            raise IllegalMoveError(move, f"no card at index {move.card_index}")
        if t.phase == "after_draw" and move.card_index != len(hand) - 1:
            raise IllegalMoveError(move, "after drawing you may only play the drawn card")
        card = hand[move.card_index]
        face = t.face(card)
        if move.card is not None and move.card != face:
            raise IllegalMoveError(
                move, f"card {move.card_index} is {face.label()}, not {move.card.label()}"
            )
        if not playable(face, t.top(), t.colour):
            raise IllegalMoveError(
                move, f"{face.label()} matches neither {t.top().label()} nor colour {t.colour}"
            )
        if face.is_wild:
            if move.declared_colour is None:
                raise IllegalMoveError(move, "a wild card must declare a colour")
            if move.declared_colour not in colours_for(t.side):
                raise IllegalMoveError(
                    move, f"{move.declared_colour} is not a {t.side}-side colour"
                )
        elif move.declared_colour is not None:
            raise IllegalMoveError(move, "only wild cards declare a colour")
        if move.called_uno and len(hand) != 2:
            raise IllegalMoveError(move, "UNO is called when playing your second-to-last card")

        hand.pop(move.card_index)
        t.discard.append(card)
        t.phase = "play"
        if len(hand) == 1 and not move.called_uno:
            t.uno_pending = seat
        prev_colour = t.colour
        t.colour = move.declared_colour if face.is_wild else face.colour
        nxt = t.next_seat(seat)
        match face.rank:
            case "draw1":
                t.give(nxt, 1)
                t.turn = t.next_seat(seat, 2)
            case "draw5":
                t.give(nxt, 5)
                t.turn = t.next_seat(seat, 2)
            case "skip":
                t.turn = t.next_seat(seat, 2)
            case "skip_all":
                t.turn = seat
            case "reverse":
                t.direction = -1 if t.direction == 1 else 1
                t.turn = seat if t.n == 2 else t.next_seat(seat)
            case "flip":
                t.flip()
                t.turn = nxt
            case "wild_draw2" | "wild_draw_colour":
                t.turn = nxt
                if hand:
                    t.phase = "wild_draw_response"
                    t.pending_from = seat
                    t.pending_prev_colour = prev_colour
                else:  # winning card: the penalty stands, nothing left to challenge
                    self._penalise(t, nxt, face.rank)
            case _:
                t.turn = nxt

    def _penalise(self, t: _Table, seat: Seat, rank: str) -> None:
        if rank == "wild_draw2":
            t.give(seat, 2)
        else:
            assert t.colour is not None
            t.give_until(seat, t.colour)

    def _respond(self, t: _Table, move: FlipMove, *, challenge: bool) -> None:
        if t.phase != "wild_draw_response" or t.pending_from is None:
            raise IllegalMoveError(move, "there is no wild draw to respond to")
        rank, victim, culprit = t.top().rank, move.seat, t.pending_from
        t.phase, t.pending_from, prev = "play", None, t.pending_prev_colour
        t.pending_prev_colour = None
        if challenge and t.has_colour(culprit, prev):
            self._penalise(t, culprit, rank)  # caught bluffing; the victim plays on
            return
        self._penalise(t, victim, rank)
        if challenge:  # failed challenge: 2 more on top of the penalty
            t.give(victim, 2)
        t.turn = t.next_seat(victim)

    def _draw(self, t: _Table, move: Draw) -> None:
        if t.phase != "play":
            raise IllegalMoveError(move, f"cannot draw in phase {t.phase}")
        if t.available() == 0:
            raise IllegalMoveError(move, "no cards left to draw")
        if t.playable_indices(t.hands[move.seat]):
            raise IllegalMoveError(move, "you have a playable card; you must play")
        t.give(move.seat, 1)
        t.phase = "after_draw"

    def _pass(self, t: _Table, move: Pass) -> None:
        if t.phase != "after_draw":
            raise IllegalMoveError(move, "you may only pass after drawing")
        t.phase = "play"
        t.turn = t.next_seat(move.seat)

    def ranking(self, state: FlipState) -> list[Seat]:
        pts = [hand_points(h, state.side) for h in state.hands]
        return sorted(range(state.seat_count), key=lambda s: (s != state.winner, pts[s], s))

    def hand_view(self, state: FlipState, seat: Seat) -> list[dict[str, Any]]:
        """Active faces only: a holder never sees the hidden face of their own cards."""
        return [
            c.face(state.side).model_dump(mode="json") | {"index": i}
            for i, c in enumerate(state.hands[seat])
        ]

    def player_view(self, state: FlipState, seat: Seat) -> PlayerView:
        side, top = state.side, state.discard[-1].face(state.side)
        pending: int | str | None = None
        if state.phase == "wild_draw_response":
            pending = 2 if top.rank == "wild_draw2" else f"colour:{state.active_colour}"
        table: dict[str, Any] = {
            "side": side,
            "discard_top": top.model_dump(mode="json"),
            "active_colour": None if state.active_colour is None else str(state.active_colour),
            "direction": state.direction,
            "pending_draw": pending,
            "uno_pending": state.uno_pending,
            "draw_pile_count": len(state.draw_pile),
            "discard_pile_count": len(state.discard),
            "winner": state.winner,
        }
        if state.phase == "after_draw" and seat == state.turn:
            table["drawn_card"] = state.hands[seat][-1].face(side).model_dump(mode="json")
        return PlayerView(
            slug=self.slug,
            seat=seat,
            phase=state.phase,
            current_seat=self.current_seat(state),
            direction=state.direction,
            hand=self.hand_view(state, seat),
            hand_counts=[len(h) for h in state.hands],
            table=table,
            legal_moves=[m.model_dump(mode="json") for m in self.legal_moves(state, seat)],
            scores=list(state.scores),
            eliminated=[],
            terminal=state.terminal,
        )

    def rules_markdown(self) -> str:
        return RULES

    def describe_move(self, move: FlipMove) -> str:
        who = f"seat {move.seat}"
        match move:
            case Play():
                label = move.card.label() if move.card else f"card {move.card_index}"
                text = f"{who} plays {label}"
                if move.declared_colour is not None:
                    text += f", declares {move.declared_colour}"
                if move.card is not None and move.card.rank == "flip":
                    text += " (the game flips to the other side)"
                if move.called_uno:
                    text += " and calls UNO"
                return text
            case Draw():
                return f"{who} draws a card"
            case Pass():
                return f"{who} passes"
            case ChallengeUno():
                return f"{who} catches seat {move.target} without UNO (draws 2)"
            case ChallengeWildDraw():
                return f"{who} challenges the wild draw"
            case AcceptWildDraw():
                return f"{who} accepts the wild draw penalty"


GAME = UnoFlip()
