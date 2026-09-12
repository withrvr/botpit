"""Hearts: one round, pass three cards left, standard penalty scoring."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any

from app.games.base import Game, IllegalMoveError, PlayerView, Seat
from app.games.cards import FrenchCard, Suit, deal, rng_for_seed, shuffled, standard_52
from app.games.hearts.moves import MOVE_ADAPTER, HeartsMove, PassCards, Play
from app.games.hearts.state import HeartsState

RULES = (Path(__file__).parent / "rules.md").read_text(encoding="utf-8")
QUEEN_OF_SPADES = FrenchCard(suit=Suit.SPADES, rank=12)
TOTAL_PENALTY = 26


def _card(suit: Suit, rank: int) -> FrenchCard:
    return FrenchCard.model_validate({"suit": suit, "rank": rank})


# Low clubs/diamonds removed so every seat gets the same number of cards.
_REMOVED: dict[int, frozenset[FrenchCard]] = {
    3: frozenset({_card(Suit.DIAMONDS, 2)}),
    4: frozenset(),
    5: frozenset({_card(Suit.DIAMONDS, 2), _card(Suit.CLUBS, 2)}),
    6: frozenset(
        {
            _card(Suit.DIAMONDS, 2),
            _card(Suit.DIAMONDS, 3),
            _card(Suit.CLUBS, 2),
            _card(Suit.CLUBS, 3),
        }
    ),
}


def deck_for(seat_count: int) -> tuple[FrenchCard, ...]:
    return tuple(c for c in standard_52() if c not in _REMOVED[seat_count])


def penalty(card: FrenchCard) -> int:
    if card.suit == Suit.HEARTS:
        return 1
    return 13 if card == QUEEN_OF_SPADES else 0


def _sorted(hand: tuple[FrenchCard, ...] | list[FrenchCard]) -> tuple[FrenchCard, ...]:
    return tuple(sorted(hand, key=lambda c: (c.suit, c.rank)))


def _penalties(taken: tuple[tuple[FrenchCard, ...], ...]) -> list[int]:
    return [sum(penalty(c) for c in cards) for cards in taken]


class Hearts(Game[HeartsState, HeartsMove]):
    slug = "hearts"
    move_adapter = MOVE_ADAPTER
    title = "Hearts"
    min_seats = 3
    max_seats = 6

    def new_game(self, seed: int, seat_count: int) -> HeartsState:
        self.check_seat_count(seat_count)
        deck = deck_for(seat_count)
        hands, _ = deal(
            shuffled(rng_for_seed(seed), deck), seat_count, len(deck) // seat_count
        )
        return HeartsState(
            slug=self.slug,
            seed=seed,
            seat_count=seat_count,
            hands=tuple(_sorted(h) for h in hands),
            scores=(0,) * seat_count,
            pending_passes=(None,) * seat_count,
            taken=((),) * seat_count,
        )

    def parse_move(self, data: dict[str, Any]) -> HeartsMove:
        return MOVE_ADAPTER.validate_python(data)

    def current_seat(self, state: HeartsState) -> Seat | None:
        if state.phase == "pass":
            return sum(p is not None for p in state.pending_passes)
        if not any(state.hands):
            return None
        return (state.leader + len(state.trick)) % state.seat_count

    def legal_moves(self, state: HeartsState, seat: Seat) -> list[HeartsMove]:
        if seat != self.current_seat(state):
            return []
        hand = state.hands[seat]
        if state.phase == "pass":
            return [
                PassCards(seat=seat, cards=c) for c in combinations(range(len(hand)), 3)
            ]
        indices, _ = self._playable(state, seat)
        return [Play(seat=seat, card_index=i, card=hand[i]) for i in indices]

    def _playable(self, state: HeartsState, seat: Seat) -> tuple[list[int], str]:
        """Indices the seat may play now, plus the reason anything else is illegal."""
        hand = state.hands[seat]
        first_trick = not any(state.taken)
        if not state.trick:
            if first_trick:
                low = min(
                    (c for h in state.hands for c in h if c.suit == Suit.CLUBS),
                    key=_rank,
                )
                return [hand.index(low)], f"the first lead must be the {low.label()}"
            if state.hearts_broken or all(c.suit == Suit.HEARTS for c in hand):
                return list(range(len(hand))), "any card"
            return [i for i, c in enumerate(hand) if c.suit != Suit.HEARTS], (
                "hearts are not broken yet: cannot lead a heart"
            )
        led = state.trick[0].suit
        follow = [i for i, c in enumerate(hand) if c.suit == led]
        if follow:
            return follow, f"must follow suit ({led})"
        if first_trick:
            safe = [i for i, c in enumerate(hand) if penalty(c) == 0]
            if safe:
                return safe, "no hearts or queen of spades on the first trick"
        return list(range(len(hand))), "any card"

    def apply(self, state: HeartsState, move: HeartsMove) -> HeartsState:
        if move.seat != self.current_seat(state):
            raise IllegalMoveError(move, "not your turn")
        if isinstance(move, PassCards):
            return self._apply_pass(state, move)
        return self._apply_play(state, move)

    def _apply_pass(self, state: HeartsState, move: PassCards) -> HeartsState:
        if state.phase != "pass":
            raise IllegalMoveError(move, "passing is over; use play")
        hand = state.hands[move.seat]
        if len(set(move.cards)) != 3:
            raise IllegalMoveError(move, "pass exactly 3 distinct cards")
        if any(not 0 <= i < len(hand) for i in move.cards):
            raise IllegalMoveError(
                move, f"card index out of range (hand has {len(hand)} cards)"
            )
        pending = list(state.pending_passes)
        pending[move.seat] = move.cards
        update: dict[str, Any] = {
            "pending_passes": tuple(pending),
            "move_count": state.move_count + 1,
        }
        picks = [p for p in pending if p is not None]
        n = state.seat_count
        if len(picks) == n:
            hands = [
                [c for i, c in enumerate(h) if i not in picks[s]]
                for s, h in enumerate(state.hands)
            ]
            for s in range(n):
                hands[(s + 1) % n].extend(state.hands[s][i] for i in picks[s])
            low = min((c for h in hands for c in h if c.suit == Suit.CLUBS), key=_rank)
            update |= {
                "hands": tuple(_sorted(h) for h in hands),
                "pending_passes": (None,) * n,
                "phase": "play",
                "leader": next(s for s in range(n) if low in hands[s]),
            }
        return state.model_copy(update=update)

    def _apply_play(self, state: HeartsState, move: Play) -> HeartsState:
        if state.phase != "play":
            raise IllegalMoveError(move, "still passing; use pass_cards")
        hand = state.hands[move.seat]
        idx = move.card_index
        if not 0 <= idx < len(hand):
            raise IllegalMoveError(move, f"no such card (hand has {len(hand)} cards)")
        if move.card is not None and move.card != hand[idx]:
            raise IllegalMoveError(
                move,
                f"card_index {idx} is {hand[idx].label()}, not {move.card.label()}",
            )
        legal, reason = self._playable(state, move.seat)
        if idx not in legal:
            raise IllegalMoveError(move, reason)
        card = hand[idx]
        n = state.seat_count
        hands = list(state.hands)
        hands[move.seat] = hand[:idx] + hand[idx + 1 :]
        trick = (*state.trick, card)
        update: dict[str, Any] = {
            "hands": tuple(hands),
            "trick": trick,
            "hearts_broken": state.hearts_broken or card.suit == Suit.HEARTS,
            "move_count": state.move_count + 1,
        }
        if len(trick) == n:
            led = trick[0].suit
            best = max(
                range(n), key=lambda i: trick[i].rank if trick[i].suit == led else 0
            )
            winner = (state.leader + best) % n
            taken = list(state.taken)
            taken[winner] = taken[winner] + trick
            points = _penalties(tuple(taken))
            if not any(hands) and TOTAL_PENALTY in points:  # shot the moon
                points = [0 if p == TOTAL_PENALTY else TOTAL_PENALTY for p in points]
            update |= {
                "trick": (),
                "leader": winner,
                "taken": tuple(taken),
                "scores": tuple(points),
            }
        return state.model_copy(update=update)

    def ranking(self, state: HeartsState) -> list[Seat]:
        return sorted(range(state.seat_count), key=lambda s: (state.scores[s], s))

    def player_view(self, state: HeartsState, seat: Seat) -> PlayerView:
        n = state.seat_count
        table: dict[str, Any]
        if state.phase == "pass":
            table = {
                "pass_direction": "left",
                "passes_done": sum(p is not None for p in state.pending_passes),
            }
        else:
            tricks_taken = [len(t) // n for t in state.taken]
            table = {
                "trick": [
                    {"seat": (state.leader + i) % n, "card": c.model_dump(mode="json")}
                    for i, c in enumerate(state.trick)
                ],
                "led_suit": state.trick[0].suit.value if state.trick else None,
                "hearts_broken": state.hearts_broken,
                "tricks_taken": tricks_taken,
                "penalty_so_far": _penalties(state.taken),
                "trick_number": sum(tricks_taken),
                "leader": state.leader,
            }
        return PlayerView(
            slug=self.slug,
            seat=seat,
            phase=state.phase,
            current_seat=self.current_seat(state),
            direction=1,
            hand=[c.model_dump(mode="json") for c in state.hands[seat]],
            hand_counts=[len(h) for h in state.hands],
            table=table,
            legal_moves=[
                m.model_dump(mode="json") for m in self.legal_moves(state, seat)
            ],
            scores=list(state.scores),
            eliminated=[],
            terminal=self.is_terminal(state),
        )

    def rules_markdown(self) -> str:
        return RULES

    def describe_move(self, move: HeartsMove) -> str:
        if isinstance(move, PassCards):
            return f"seat {move.seat} passes 3 cards"
        what = move.card.label() if move.card is not None else f"card {move.card_index}"
        return f"seat {move.seat} plays {what}"


def _rank(card: FrenchCard) -> int:
    return card.rank


GAME = Hearts()
