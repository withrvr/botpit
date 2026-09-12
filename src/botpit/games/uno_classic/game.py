from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from botpit.games.base import Game, IllegalMoveError, PlayerView, Seat
from botpit.games.cards import (
    LIGHT_COLOURS,
    Colour,
    UnoCard,
    deal,
    rng_for,
    rng_for_seed,
    shuffled,
)
from botpit.games.uno_classic.moves import (
    MOVE_ADAPTER,
    AcceptDraw4,
    ChallengeDraw4,
    ChallengeUno,
    Draw,
    Pass,
    Play,
    UnoMove,
)
from botpit.games.uno_classic.state import UnoState

if TYPE_CHECKING:
    import random

RULES = (Path(__file__).parent / "rules.md").read_text(encoding="utf-8")
HAND_SIZE = 7
WILD = "wild"
WILD_DRAW4 = "wild_draw4"
ACTION_RANKS = ("skip", "reverse", "draw2")


def build_deck() -> tuple[UnoCard, ...]:
    cards: list[UnoCard] = []
    for colour in LIGHT_COLOURS:
        cards.append(UnoCard(colour=colour, rank="0"))
        for rank in (*(str(n) for n in range(1, 10)), *ACTION_RANKS):
            cards += [UnoCard(colour=colour, rank=rank)] * 2
    cards += [UnoCard(colour=None, rank=WILD)] * 4
    cards += [UnoCard(colour=None, rank=WILD_DRAW4)] * 4
    return tuple(cards)


def card_points(card: UnoCard) -> int:
    if card.is_wild:
        return 50
    return int(card.rank) if card.is_number else 20


def matches(card: UnoCard, top: UnoCard, active_colour: Colour | None) -> bool:
    return (
        card.is_wild
        or active_colour is None
        or card.colour == active_colour
        or card.rank == top.rank
    )


def _draw(draw: list[UnoCard], discard: list[UnoCard], n: int, rng: random.Random) -> list[UnoCard]:
    """Pop ``n`` cards, reshuffling the discard pile (minus its top) into the draw
    pile when it runs dry. Stops short when neither pile can supply a card."""
    out: list[UnoCard] = []
    for _ in range(n):
        if not draw and len(discard) > 1:
            top = discard.pop()
            draw[:] = shuffled(rng, discard)
            discard[:] = [top]
        if not draw:
            break
        out.append(draw.pop())
    return out


def _can_draw(state: UnoState) -> bool:
    return bool(state.draw_pile) or len(state.discard_pile) > 1


def _uno_pending(state: UnoState) -> list[Seat]:
    return [s for s, hand in enumerate(state.hands) if len(hand) == 1 and not state.uno_called[s]]


class UnoClassic(Game[UnoState, UnoMove]):
    slug = "uno_classic"
    title = "Classic UNO"
    min_seats = 2
    max_seats = 10

    def new_game(self, seed: int, seat_count: int) -> UnoState:
        self.check_seat_count(seat_count)
        rng = rng_for_seed(seed)
        dealt, rest = deal(shuffled(rng, build_deck()), seat_count, HAND_SIZE)
        hands = [list(h) for h in dealt]
        draw = list(rest)
        first = draw.pop()
        while first.rank == WILD_DRAW4:
            draw = list(shuffled(rng, [*draw, first]))
            first = draw.pop()
        discard = [first]
        turn, direction = 0, 1
        if first.rank == "skip":
            turn = 1
        elif first.rank == "reverse":
            direction = -1
            turn = 1 if seat_count == 2 else 0
        elif first.rank == "draw2":
            hands[0] += _draw(draw, discard, 2, rng)
            turn = 1
        state = UnoState(
            slug=self.slug,
            seed=seed,
            seat_count=seat_count,
            hands=tuple(tuple(h) for h in hands),
            scores=(0,) * seat_count,
            draw_pile=tuple(draw),
            discard_pile=tuple(discard),
            active_colour=first.colour,
            direction=direction,
            turn=turn,
            uno_called=(True,) * seat_count,
        )
        return state.model_copy(update={"scores": self._scores(state)})

    def parse_move(self, data: dict[str, Any]) -> UnoMove:
        return MOVE_ADAPTER.validate_python(data)

    def current_seat(self, state: UnoState) -> Seat | None:
        return None if state.terminal else state.turn

    def _plays(self, state: UnoState, seat: Seat, indices: range) -> list[UnoMove]:
        hand = state.hands[seat]
        uno_options = (False, True) if len(hand) == 2 else (False,)
        moves: list[UnoMove] = []
        for i in indices:
            card = hand[i]
            if not matches(card, state.top, state.active_colour):
                continue
            colours: tuple[Colour | None, ...] = LIGHT_COLOURS if card.is_wild else (None,)
            moves += [
                Play(seat=seat, card_index=i, declared_colour=c, called_uno=u, card=card)
                for c in colours
                for u in uno_options
            ]
        return moves

    def legal_moves(self, state: UnoState, seat: Seat) -> list[UnoMove]:
        if seat != self.current_seat(state):
            return []
        hand = state.hands[seat]
        if state.phase == "draw4_response":
            return [AcceptDraw4(seat=seat), ChallengeDraw4(seat=seat)]
        if state.phase == "after_draw":
            return [*self._plays(state, seat, range(len(hand) - 1, len(hand))), Pass(seat=seat)]
        moves: list[UnoMove] = [
            ChallengeUno(seat=seat, target=t) for t in _uno_pending(state) if t != seat
        ]
        moves += self._plays(state, seat, range(len(hand)))
        if _can_draw(state):
            moves.append(Draw(seat=seat))
        return moves

    def apply(self, state: UnoState, move: UnoMove) -> UnoState:
        if move.seat != self.current_seat(state):
            raise IllegalMoveError(move, "not your turn")
        seat, n = move.seat, state.seat_count
        hands = [list(h) for h in state.hands]
        hand = hands[seat]
        draw, discard = list(state.draw_pile), list(state.discard_pile)
        rng = rng_for(state)
        active, direction = state.active_colour, state.direction
        turn, phase = state.turn, state.phase
        uno_called = list(state.uno_called)
        draw4_player, draw4_prev = state.draw4_player, state.draw4_prev_colour
        terminal, winner = False, None

        def advance(steps: int) -> Seat:
            return (seat + direction * steps) % n

        def close_uno_windows() -> None:
            uno_called[:] = [True] * n

        if isinstance(move, ChallengeUno):
            if phase != "play":
                raise IllegalMoveError(move, "UNO can only be challenged at the start of your turn")
            if move.target == seat or not 0 <= move.target < n:
                raise IllegalMoveError(move, "no such opponent")
            if len(hands[move.target]) != 1 or uno_called[move.target]:
                raise IllegalMoveError(move, f"seat {move.target} cannot be caught out")
            hands[move.target] += _draw(draw, discard, 2, rng)
            uno_called[move.target] = True
        elif isinstance(move, AcceptDraw4 | ChallengeDraw4):
            if phase != "draw4_response" or draw4_player is None:
                raise IllegalMoveError(move, "no Wild Draw Four is pending against you")
            illegal = (
                isinstance(move, ChallengeDraw4)
                and draw4_prev is not None
                and any(c.colour == draw4_prev for c in hands[draw4_player])
            )
            if illegal:
                hands[draw4_player] += _draw(draw, discard, 4, rng)
                turn = seat
            else:
                penalty = 6 if isinstance(move, ChallengeDraw4) else 4
                hand += _draw(draw, discard, penalty, rng)
                turn = advance(1)
            phase, draw4_player, draw4_prev = "play", None, None
        elif isinstance(move, Draw):
            if phase != "play":
                raise IllegalMoveError(move, "you already drew this turn")
            if not _can_draw(state):
                raise IllegalMoveError(move, "no cards left to draw")
            hand += _draw(draw, discard, 1, rng)
            close_uno_windows()
            phase = "after_draw"
        elif isinstance(move, Pass):
            if phase != "after_draw":
                raise IllegalMoveError(move, "you may only pass after drawing a card")
            close_uno_windows()
            phase, turn = "play", advance(1)
        else:
            if phase == "draw4_response":
                raise IllegalMoveError(move, "accept or challenge the Wild Draw Four first")
            if not 0 <= move.card_index < len(hand):
                raise IllegalMoveError(move, "no such card")
            card = hand[move.card_index]
            if move.card is not None and move.card != card:
                raise IllegalMoveError(move, f"card {move.card_index} is {card.label()}")
            if phase == "after_draw" and move.card_index != len(hand) - 1:
                raise IllegalMoveError(move, "after drawing you may only play the drawn card")
            if not matches(card, state.top, active):
                raise IllegalMoveError(move, f"{card.label()} matches neither colour nor rank")
            if card.is_wild and move.declared_colour not in LIGHT_COLOURS:
                raise IllegalMoveError(move, "a wild must declare red, yellow, green or blue")
            if not card.is_wild and move.declared_colour is not None:
                raise IllegalMoveError(move, "only wilds declare a colour")
            if move.called_uno and len(hand) != 2:
                raise IllegalMoveError(move, "call UNO only on the play that leaves you one card")
            hand.pop(move.card_index)
            discard.append(card)
            prev_colour, active = active, move.declared_colour or card.colour
            close_uno_windows()
            uno_called[seat] = move.called_uno
            phase = "play"
            if not hand:
                terminal, winner = True, seat
            nxt = advance(1)
            if card.rank == "skip":
                turn = advance(2)
            elif card.rank == "reverse":
                direction = -direction
                turn = advance(2 if n == 2 else 1)
            elif card.rank == "draw2":
                hands[nxt] += _draw(draw, discard, 2, rng)
                turn = advance(2)
            elif card.rank == WILD_DRAW4 and not terminal:
                phase, turn, draw4_player, draw4_prev = "draw4_response", nxt, seat, prev_colour
            elif card.rank == WILD_DRAW4:
                hands[nxt] += _draw(draw, discard, 4, rng)
            else:
                turn = nxt

        nxt_state = state.model_copy(
            update={
                "hands": tuple(tuple(h) for h in hands),
                "draw_pile": tuple(draw),
                "discard_pile": tuple(discard),
                "active_colour": active,
                "direction": direction,
                "turn": turn,
                "phase": phase,
                "uno_called": tuple(uno_called),
                "draw4_player": draw4_player,
                "draw4_prev_colour": draw4_prev,
                "terminal": terminal,
                "winner": winner,
                "move_count": state.move_count + 1,
            }
        )
        if not terminal and phase == "play" and not self.legal_moves(nxt_state, turn):
            nxt_state = nxt_state.model_copy(update={"terminal": True})
        return nxt_state.model_copy(update={"scores": self._scores(nxt_state)})

    @staticmethod
    def _scores(state: UnoState) -> tuple[int, ...]:
        return tuple(sum(card_points(c) for c in hand) for hand in state.hands)

    def ranking(self, state: UnoState) -> list[Seat]:
        return sorted(
            range(state.seat_count), key=lambda s: (s != state.winner, state.scores[s], s)
        )

    def player_view(self, state: UnoState, seat: Seat) -> PlayerView:
        table: dict[str, Any] = {
            "discard_top": state.top.model_dump(mode="json"),
            "active_colour": state.active_colour,
            "direction": state.direction,
            "pending_draw": 4 if state.phase == "draw4_response" else 0,
            "uno_pending": _uno_pending(state),
            "draw_pile_count": len(state.draw_pile),
            "discard_pile_count": len(state.discard_pile),
        }
        if state.phase == "after_draw" and seat == state.turn:
            table["drawn_card"] = state.hands[seat][-1].model_dump(mode="json")
        return PlayerView(
            slug=self.slug,
            seat=seat,
            phase=state.phase,
            current_seat=self.current_seat(state),
            direction=state.direction,
            hand=[c.model_dump(mode="json") for c in state.hands[seat]],
            hand_counts=[len(h) for h in state.hands],
            table=table,
            legal_moves=[m.model_dump(mode="json") for m in self.legal_moves(state, seat)],
            scores=list(state.scores),
            eliminated=list(state.eliminated),
            terminal=state.terminal,
        )

    def rules_markdown(self) -> str:
        return RULES

    def describe_move(self, move: UnoMove) -> str:
        match move:
            case Play(card=None):
                text = f"plays card {move.card_index}"
            case Play(card=UnoCard() as card):
                text = f"plays {card.label()}"
            case Draw():
                text = "draws a card"
            case Pass():
                text = "passes"
            case ChallengeUno():
                text = f"catches seat {move.target} not saying UNO"
            case ChallengeDraw4():
                text = "challenges the Wild Draw Four"
            case AcceptDraw4():
                text = "accepts the Wild Draw Four"
        if isinstance(move, Play):
            if move.declared_colour is not None:
                text += f" (chooses {move.declared_colour})"
            if move.called_uno:
                text += " and calls UNO"
        return f"seat {move.seat} {text}"


GAME = UnoClassic()
