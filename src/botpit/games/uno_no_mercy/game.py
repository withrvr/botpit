"""UNO Show 'em No Mercy rules engine (official Mattel rules, single round)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

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
from botpit.games.uno_no_mercy.moves import (
    MOVE_ADAPTER,
    ChallengeUno,
    Draw,
    NoMercyMove,
    Pass,
    Play,
)
from botpit.games.uno_no_mercy.state import NoMercyState

if TYPE_CHECKING:
    import random

HAND_SIZE = 7
MERCY_LIMIT = 25
ELIMINATED_POINTS = 250
DRAW_VALUE = {"draw2": 2, "draw4": 4, "wild_draw6": 6, "wild_draw10": 10, "wild_reverse_draw4": 4}
COLOUR_ACTIONS = ("skip", "reverse", "draw2", "draw4", "skip_all", "discard_all")
WILDS = ("wild_reverse_draw4", "wild_draw6", "wild_draw10", "wild_roulette")
RANK_NAMES = {
    "skip": "Skip",
    "reverse": "Reverse",
    "draw2": "Draw Two",
    "draw4": "Draw Four",
    "skip_all": "Skip Everyone",
    "discard_all": "Discard All",
    "wild_reverse_draw4": "Wild Reverse Draw Four",
    "wild_draw6": "Wild Draw Six",
    "wild_draw10": "Wild Draw Ten",
    "wild_roulette": "Wild Colour Roulette",
}


def build_deck() -> tuple[UnoCard, ...]:
    cards: list[UnoCard] = []
    for colour in LIGHT_COLOURS:
        for n in range(10):
            cards += [UnoCard(colour=colour, rank=str(n))] * 2
        for rank in ("skip", "reverse", "draw2"):
            cards += [UnoCard(colour=colour, rank=rank)] * 2
        for rank in ("draw4", "skip_all", "discard_all"):
            cards += [UnoCard(colour=colour, rank=rank)] * 4
    for rank in WILDS:
        cards += [UnoCard(colour=None, rank=rank)] * 4
    return tuple(cards)


def card_points(card: UnoCard) -> int:
    if card.is_number:
        return int(card.rank)
    return 50 if card.is_wild else 20


def card_name(card: UnoCard) -> str:
    rank = RANK_NAMES.get(card.rank, card.rank)
    return rank if card.colour is None else f"{card.colour} {rank}"


class _Work:
    """Mutable scratch copy of a state for one ``apply``; ``commit`` freezes it."""

    def __init__(self, state: NoMercyState) -> None:
        self.state = state
        self.hands = [list(h) for h in state.hands]
        self.draw_pile = list(state.draw_pile)
        self.discard = list(state.discard_pile)
        self.eliminated = list(state.eliminated)
        self.direction: Literal[1, -1] = state.direction
        self.turn = state.turn
        self.pending = state.pending_draw
        self.active_colour = state.active_colour
        self.uno_pending: Seat | None = None  # any action closes the previous window
        self.winner = state.winner
        self._rng: random.Random | None = None

    @property
    def active(self) -> list[Seat]:
        return [s for s in range(self.state.seat_count) if s not in self.eliminated]

    def draw_cards(self, seat: Seat, n: int) -> list[UnoCard]:
        """Move up to ``n`` cards from the draw pile to ``seat``; reshuffles the discard
        pile (all but its top) when the draw pile runs dry. No mercy check."""
        drawn: list[UnoCard] = []
        for _ in range(n):
            if not self.draw_pile:
                if len(self.discard) < 2:
                    break
                if self._rng is None:
                    self._rng = rng_for(self.state)
                self.draw_pile = list(shuffled(self._rng, self.discard[:-1]))
                del self.discard[:-1]
            drawn.append(self.draw_pile.pop(0))
        self.hands[seat] += drawn
        return drawn

    def mercy(self, seat: Seat) -> None:
        if len(self.hands[seat]) < MERCY_LIMIT:
            return
        self.draw_pile += self.hands[seat]
        self.hands[seat] = []
        self.eliminated.append(seat)
        if len(self.active) == 1:
            self.winner = self.active[0]

    def draw(self, seat: Seat, n: int) -> None:
        self.draw_cards(seat, n)
        self.mercy(seat)

    def advance(self, steps: int) -> None:
        seat = self.turn
        for _ in range(steps):
            seat = (seat + self.direction) % self.state.seat_count
            while seat in self.eliminated:
                seat = (seat + self.direction) % self.state.seat_count
        self.turn = seat

    def flip(self) -> None:
        self.direction = -1 if self.direction == 1 else 1

    def commit(self, *, after_draw: bool = False) -> NoMercyState:
        hands = tuple(tuple(h) for h in self.hands)
        return self.state.model_copy(
            update={
                "hands": hands,
                "draw_pile": tuple(self.draw_pile),
                "discard_pile": tuple(self.discard),
                "eliminated": tuple(self.eliminated),
                "direction": self.direction,
                "turn": self.turn,
                "pending_draw": self.pending,
                "active_colour": self.active_colour,
                "after_draw": after_draw,
                "uno_pending": self.uno_pending,
                "winner": self.winner,
                "scores": _scores(hands, self.eliminated),
                "move_count": self.state.move_count + 1,
            }
        )


def _scores(hands: tuple[tuple[UnoCard, ...], ...], eliminated: list[Seat]) -> tuple[int, ...]:
    return tuple(
        ELIMINATED_POINTS if s in eliminated else sum(card_points(c) for c in hand)
        for s, hand in enumerate(hands)
    )


class NoMercy(Game[NoMercyState, NoMercyMove]):
    slug = "uno_no_mercy"
    title = "UNO Show 'em No Mercy"
    min_seats = 2
    max_seats = 10

    def new_game(self, seed: int, seat_count: int) -> NoMercyState:
        self.check_seat_count(seat_count)
        hands, rest = deal(shuffled(rng_for_seed(seed), build_deck()), seat_count, HAND_SIZE)
        pile = list(rest)
        skipped: list[UnoCard] = []
        while not pile[0].is_number:
            skipped.append(pile.pop(0))
        first = pile.pop(0)
        assert first.colour is not None
        return NoMercyState(
            slug=self.slug,
            seed=seed,
            seat_count=seat_count,
            hands=hands,
            scores=_scores(hands, []),
            draw_pile=tuple(pile + skipped),
            discard_pile=(first,),
            active_colour=first.colour,
        )

    def parse_move(self, data: dict[str, Any]) -> NoMercyMove:
        return MOVE_ADAPTER.validate_python(data)

    def current_seat(self, state: NoMercyState) -> Seat | None:
        return None if state.winner is not None or state.stalled else state.turn

    # -- legality -----------------------------------------------------------

    @staticmethod
    def _matches(state: NoMercyState, card: UnoCard) -> bool:
        return card.is_wild or card.colour == state.active_colour or card.rank == state.top.rank

    @staticmethod
    def _drawable(state: NoMercyState) -> bool:
        return len(state.draw_pile) + len(state.discard_pile) > 1

    @staticmethod
    def _remaining(hand: tuple[UnoCard, ...], index: int) -> int:
        """Cards left after playing ``hand[index]`` (Discard All takes its colour along)."""
        card = hand[index]
        if card.rank == "discard_all":
            return sum(1 for c in hand if c.colour != card.colour)
        return len(hand) - 1

    def _play_variants(self, state: NoMercyState, seat: Seat, index: int) -> list[Play]:
        hand = state.hands[seat]
        card = hand[index]
        colours: tuple[Colour | None, ...] = LIGHT_COLOURS if card.is_wild else (None,)
        targets: list[Seat | None] = (
            [t for t in state.active_seats if t != seat] if card.rank == "7" else [None]
        )
        unos = (False, True) if self._remaining(hand, index) == 1 else (False,)
        return [
            Play(
                seat=seat,
                card_index=index,
                declared_colour=colour,
                called_uno=uno,
                target_seat=target,
                card=card,
            )
            for colour in colours
            for target in targets
            for uno in unos
        ]

    def legal_moves(self, state: NoMercyState, seat: Seat) -> list[NoMercyMove]:
        if seat != self.current_seat(state):
            return []
        hand = state.hands[seat]
        moves: list[NoMercyMove] = []
        if state.pending_draw:
            floor = DRAW_VALUE[state.top.rank]
            for i, card in enumerate(hand):
                if DRAW_VALUE.get(card.rank, 0) >= floor:
                    moves += self._play_variants(state, seat, i)
            moves.append(Draw(seat=seat, count=state.pending_draw))
        elif state.after_draw:
            if self._matches(state, hand[-1]):
                moves += self._play_variants(state, seat, len(hand) - 1)
            moves.append(Pass(seat=seat))
        else:
            for i, card in enumerate(hand):
                if self._matches(state, card):
                    moves += self._play_variants(state, seat, i)
            if self._drawable(state):
                moves.append(Draw(seat=seat, count=1))
        target = self._challengeable(state, seat)
        if target is not None:
            moves.append(ChallengeUno(seat=seat, target=target))
        return moves

    @staticmethod
    def _challengeable(state: NoMercyState, seat: Seat) -> Seat | None:
        t = state.uno_pending
        if t is None or t == seat or t in state.eliminated or state.after_draw:
            return None
        return t

    # -- apply --------------------------------------------------------------

    def apply(self, state: NoMercyState, move: NoMercyMove) -> NoMercyState:
        if move.seat != self.current_seat(state):
            raise IllegalMoveError(move, "not your turn")
        match move:
            case Play():
                new = self._apply_play(state, move)
            case Draw():
                new = self._apply_draw(state, move)
            case Pass():
                if not state.after_draw:
                    raise IllegalMoveError(move, "you may only pass right after drawing a card")
                w = _Work(state)
                w.advance(1)
                new = w.commit()
            case ChallengeUno():
                new = self._apply_challenge(state, move)
        if new.winner is None and not self.legal_moves(new, new.turn):
            new = new.model_copy(update={"stalled": True})
        return new

    def _apply_play(self, state: NoMercyState, move: Play) -> NoMercyState:
        seat = move.seat
        hand = state.hands[seat]
        if not 0 <= move.card_index < len(hand):
            raise IllegalMoveError(move, f"no card at index {move.card_index}")
        card = hand[move.card_index]
        if move.card is not None and move.card != card:
            msg = f"card {move.card_index} is {card_name(card)}, not {card_name(move.card)}"
            raise IllegalMoveError(move, msg)
        if state.pending_draw:
            floor = DRAW_VALUE[state.top.rank]
            if DRAW_VALUE.get(card.rank, 0) < floor:
                msg = (
                    f"you face a draw of {state.pending_draw}: stack a draw card worth "
                    f"{floor} or more, or draw"
                )
                raise IllegalMoveError(move, msg)
        elif state.after_draw and move.card_index != len(hand) - 1:
            raise IllegalMoveError(move, "after drawing you may only play the drawn card or pass")
        elif not self._matches(state, card):
            msg = (
                f"{card_name(card)} matches neither {card_name(state.top)} "
                f"nor the active colour {state.active_colour}"
            )
            raise IllegalMoveError(move, msg)
        if card.is_wild:
            if move.declared_colour is None:
                raise IllegalMoveError(move, "a wild card needs declared_colour")
            if move.declared_colour not in LIGHT_COLOURS:
                raise IllegalMoveError(move, f"declared_colour must be one of {LIGHT_COLOURS}")
        elif move.declared_colour is not None:
            raise IllegalMoveError(move, "declared_colour is only for wild cards")
        if card.rank == "7":
            if move.target_seat is None:
                raise IllegalMoveError(move, "a 7 needs target_seat: the seat to swap hands with")
            if move.target_seat == seat or move.target_seat not in state.active_seats:
                raise IllegalMoveError(move, f"cannot swap hands with seat {move.target_seat}")
        elif move.target_seat is not None:
            raise IllegalMoveError(move, "target_seat is only for a 7")
        remaining = self._remaining(hand, move.card_index)
        if move.called_uno and remaining != 1:
            raise IllegalMoveError(move, "called_uno only when this play leaves you one card")

        w = _Work(state)
        my = w.hands[seat]
        del my[move.card_index]
        if card.rank == "discard_all":
            w.discard += [c for c in my if c.colour == card.colour]
            my[:] = [c for c in my if c.colour != card.colour]
        w.discard.append(card)
        w.active_colour = move.declared_colour or card.colour  # type: ignore[assignment]
        if remaining == 1 and not move.called_uno:
            w.uno_pending = seat
        won = remaining == 0
        two = len(w.active) == 2
        steps = 1
        match card.rank:
            case "7" if not won:
                t = move.target_seat
                assert t is not None
                w.hands[seat], w.hands[t] = w.hands[t], w.hands[seat]
            case "0" if not won:
                order = w.active
                old = [w.hands[s] for s in order]
                for i in range(len(order)):
                    w.hands[order[(i + w.direction) % len(order)]] = old[i]
            case "skip":
                steps = 2
            case "reverse":
                if two:
                    steps = 0
                else:
                    w.flip()
            case "skip_all":
                steps = 0
            case "wild_reverse_draw4":
                w.flip()
                w.pending += 4
                if two:
                    steps = 0
            case "wild_roulette":
                w.advance(1)
                victim = w.turn
                colour = move.declared_colour
                while (drawn := w.draw_cards(victim, 1)) and drawn[0].colour != colour:
                    pass
                w.mercy(victim)
            case rank if rank in DRAW_VALUE:
                w.pending += DRAW_VALUE[rank]
        w.advance(steps)
        if won:
            w.winner = seat
        return w.commit()

    def _apply_draw(self, state: NoMercyState, move: Draw) -> NoMercyState:
        w = _Work(state)
        seat = move.seat
        if state.pending_draw:
            w.draw(seat, state.pending_draw)
            w.pending = 0
            w.advance(1)
            return w.commit()
        if state.after_draw:
            raise IllegalMoveError(move, "you already drew: play the drawn card or pass")
        if not self._drawable(state):
            raise IllegalMoveError(move, "no cards left to draw")
        w.draw(seat, 1)
        if seat in w.eliminated:
            w.advance(1)
            return w.commit()
        return w.commit(after_draw=True)

    def _apply_challenge(self, state: NoMercyState, move: ChallengeUno) -> NoMercyState:
        if self._challengeable(state, move.seat) != move.target:
            raise IllegalMoveError(move, f"seat {move.target} is not open to an UNO challenge")
        w = _Work(state)
        w.draw(move.target, 2)
        return w.commit()

    # -- reporting ----------------------------------------------------------

    def ranking(self, state: NoMercyState) -> list[Seat]:
        def key(s: Seat) -> tuple[int, int, int]:
            if s == state.winner:
                return (0, 0, s)
            if s in state.eliminated:
                return (2, -state.eliminated.index(s), s)
            return (1, state.scores[s], s)

        return sorted(range(state.seat_count), key=key)

    def player_view(self, state: NoMercyState, seat: Seat) -> PlayerView:
        hand = state.hands[seat]
        table: dict[str, Any] = {
            "discard_top": state.top.model_dump(mode="json"),
            "active_colour": state.active_colour,
            "direction": state.direction,
            "pending_draw": state.pending_draw,
            "uno_pending": state.uno_pending,
            "draw_pile_count": len(state.draw_pile),
            "discard_pile_count": len(state.discard_pile),
            "eliminated": list(state.eliminated),
        }
        if state.after_draw and seat == state.turn:
            table["drawn_card"] = hand[-1].model_dump(mode="json")
        return PlayerView(
            slug=self.slug,
            seat=seat,
            phase=state.phase,
            current_seat=self.current_seat(state),
            direction=state.direction,
            hand=[c.model_dump(mode="json") for c in hand],
            hand_counts=[len(h) for h in state.hands],
            table=table,
            legal_moves=[m.model_dump(mode="json") for m in self.legal_moves(state, seat)],
            scores=list(state.scores),
            eliminated=list(state.eliminated),
            terminal=self.is_terminal(state),
        )

    def rules_markdown(self) -> str:
        return Path(__file__).with_name("rules.md").read_text(encoding="utf-8")

    def describe_move(self, move: NoMercyMove) -> str:
        match move:
            case Play():
                what = card_name(move.card) if move.card else f"card {move.card_index}"
                text = f"seat {move.seat} plays {what}"
                if move.declared_colour:
                    text += f" (chooses {move.declared_colour})"
                if move.target_seat is not None:
                    text += f" and swaps hands with seat {move.target_seat}"
                return text + (" and calls UNO" if move.called_uno else "")
            case Draw():
                if move.count is None:
                    return f"seat {move.seat} draws"
                what = "a card" if move.count == 1 else f"{move.count} cards"
                return f"seat {move.seat} draws {what}"
            case Pass():
                return f"seat {move.seat} passes"
            case ChallengeUno():
                return f"seat {move.seat} challenges seat {move.target} for not calling UNO"


GAME = NoMercy()
