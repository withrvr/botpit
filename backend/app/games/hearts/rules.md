# Hearts

A trick-taking game for 3-6 seats, one round. You want the **fewest penalty
points**: every heart you win is 1 point and the queen of spades is 13, for
26 points in the deck. Lowest total wins; ties go to the lower seat index.

## Cards

Suits: `clubs`, `diamonds`, `hearts`, `spades`. Ranks: `2`-`10`, `11` = J,
`12` = Q, `13` = K, `14` = A. Ace is high. A card is a JSON object like
`{"suit": "spades", "rank": 12}` (the queen of spades).

The deck is trimmed so every seat gets the same number of cards. Removed
cards are never penalty cards, so the round always holds exactly 26 points.

| seats | removed                | cards each | first lead   |
|-------|------------------------|------------|--------------|
| 3     | 2 of diamonds          | 17         | 2 of clubs   |
| 4     | none                   | 13         | 2 of clubs   |
| 5     | 2 of diamonds, 2 of clubs | 10      | 3 of clubs   |
| 6     | 2 and 3 of diamonds, 2 and 3 of clubs | 8 | 4 of clubs |

Your `hand` is always sorted by suit (clubs, diamonds, hearts, spades) then
rank, and `card_index` is the 0-based position in that list. Indices shift
whenever a card leaves your hand, so always read them from the current view.

## Phase `pass`

Before play, every seat passes 3 cards to the seat on its left
(seat `s` gives to seat `(s + 1) % seat_count`). Seats choose in order
0, 1, 2, ...; nobody's choice is applied or revealed until the last seat has
chosen, then all passes happen at once and hands are re-sorted. You will
never see what was passed to you until it appears in your hand.

Move: `{"kind": "pass_cards", "seat": 1, "cards": [0, 5, 12]}` - three
**distinct** indices into your own hand, any order.

`table` during this phase: `{"pass_direction": "left", "passes_done": 2}`.

## Phase `play`

The seat holding the lowest club in the deck (see table) leads it; that is
their only legal first move. Play goes clockwise (seat + 1). After each
trick of `seat_count` cards, the winner takes the cards and leads next.

Move: `{"kind": "play", "seat": 2, "card_index": 4}`. You may also include
the `card` you mean (`"card": {"suit": "spades", "rank": 12}`); if it does
not match `card_index` the move is rejected.

Rules for the card you play, in order:

1. **Follow suit.** If you hold any card of the suit that was led, you must
   play one of them. The highest card of the led suit wins the trick;
   cards of other suits never win.
2. **Void in the led suit?** Play anything - this is how you dump hearts or
   the queen of spades on someone. Exception: on the **first trick** you may
   not discard a heart or the queen of spades unless your hand contains
   nothing else.
3. **Leading.** You may not lead a heart until hearts are **broken** (a
   heart has been played on an earlier trick) unless your hand is all
   hearts. The queen of spades does *not* break hearts.

`legal_moves` in your view already applies all of this.

**Output contract:** reply with exactly one JSON object, one of the kinds below, and it must be one of the entries in `legal_moves`.

`table` during play:

```json
{
  "trick": [{"seat": 3, "card": {"suit": "clubs", "rank": 2}}, {"seat": 0, "card": {"suit": "clubs", "rank": 10}}],
  "led_suit": "clubs",
  "hearts_broken": false,
  "tricks_taken": [0, 0, 0, 0],
  "penalty_so_far": [0, 0, 0, 0],
  "trick_number": 0,
  "leader": 3
}
```

`trick` lists the cards on the table so far this trick in play order;
`led_suit` is `null` when you are leading. `trick_number` counts completed
tricks. `penalty_so_far` is the points each seat has already been stuck with.

## Scoring

After the last trick: each heart taken is 1 point, the queen of spades is 13.
`scores` are penalty points - **lower is better** - and are updated after
every completed trick.

**Shooting the moon:** a seat that takes *all* 26 points scores 0 and every
other seat scores 26 instead.

The game ends when all hands are empty. Ranking: ascending points, ties by
seat index.

## How to think

- Passing: get rid of high spades (A, K, Q unless you hold many spades),
  high hearts, and try to void a suit so you can discard early.
- Track `hearts_broken`, `penalty_so_far` and which suits others have
  failed to follow (they are void there and will dump on you).
- Take early tricks with low risk (before hearts are broken) to unload high
  cards; avoid winning tricks once opponents are void in the led suit.
- The queen of spades is the whole game: if you hold it, wait until you can
  discard it; if an opponent has it, avoid leading spades with a high card.
- Shooting the moon is rare; only try with a very strong hand of high cards
  in every suit. If someone else has taken every point so far, take a heart
  to stop them.
