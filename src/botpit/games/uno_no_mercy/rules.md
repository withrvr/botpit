# UNO Show 'em No Mercy

2-10 seats, one round. Empty your hand first, or be the last seat standing.
Seats are numbered from 0. Play moves clockwise (`direction` 1: seat 0, 1, 2, ...)
until a Reverse flips it (`direction` -1).

## The deck (168 cards)

Each card is `{"colour": ..., "rank": ...}`. Colours: `red`, `yellow`, `green`, `blue`;
wilds have `"colour": null`.

Per colour (38 cards x 4 colours = 152):

| rank | count | effect |
|---|---|---|
| `"0"`-`"9"` | 2 each | number. **0**: every seat passes its whole hand to the next seat in the current direction. **7**: you swap your whole hand with a seat you choose. Both are mandatory. |
| `"skip"` | 2 | next seat loses its turn |
| `"reverse"` | 2 | direction flips (with 2 seats it acts as a Skip) |
| `"draw2"` | 2 | next seat faces a draw of 2 (stackable) |
| `"draw4"` | 4 | next seat faces a draw of 4 (stackable) |
| `"skip_all"` | 4 | Skip Everyone: every other seat is skipped, you play again |
| `"discard_all"` | 4 | Discard All: it leaves your hand together with every card of its colour |

Wilds (4 each = 16). There is no plain Wild and no plain Wild Draw Four:

| rank | effect |
|---|---|
| `"wild_reverse_draw4"` | declare a colour, direction flips, and the seat that is now next (the one who played before you) faces a draw of 4 (stackable). With 2 seats **you** face the 4 yourself (and may stack onto it). |
| `"wild_draw6"` | declare a colour; next seat faces a draw of 6 (stackable) |
| `"wild_draw10"` | declare a colour; next seat faces a draw of 10 (stackable) |
| `"wild_roulette"` | Wild Colour Roulette: declare a colour; the next seat draws cards until it draws one of that colour, then loses its turn |

## Setup and matching

Everyone gets 7 cards. The first discard is the first number card off the deck
(action cards turned up at that point go under the draw pile).

On your turn you may play a card that matches the discard top by **colour**
(the `active_colour`, which after a wild is the declared colour), by **number**,
or by **symbol** (e.g. any Skip on a Skip, any Discard All on a Discard All).
Wilds can always be played. Otherwise you draw one card; if it is playable you
may play it at once, else you pass.

## Stacking draws (`pending_draw`)

When a draw card is played the penalty accumulates in `pending_draw` and the
next seat must respond. Only two moves are legal while `pending_draw > 0`:

1. **Stack**: play a draw card worth the same or more than the card on top
   (`draw2`=2, `draw4`=4, `wild_reverse_draw4`=4, `wild_draw6`=6, `wild_draw10`=10).
   Colour does **not** matter for stacking, only the value. The values add up and
   the whole total moves to the next seat (a Wild Reverse Draw Four sends it back
   the other way). Phase is `draw_response` while you face a stack.
2. **Draw**: take the whole pending total and lose your turn.

You cannot stack a lower card (no `draw2` on a `draw4`). A stacked wild still
needs `declared_colour`.

## Mercy rule and elimination

The moment any draw leaves a seat holding **25 or more cards**, that seat is
eliminated: its cards go under the draw pile, it is skipped in turn order and
cannot be a 7 target. If only one seat remains, it wins.

## Winning and scoring

The game ends when a seat plays its last card (a Discard All may do this) or
when only one seat survives. If the seat to move has no playable card and there
are no cards left to draw, the game ends and the ranking decides.

Points still held count against you: numbers face value, coloured action cards
20, wilds 50, eliminated seats 250. Ranking: winner, then survivors by fewest
points, then eliminated seats (last out ranks highest).

## UNO

When a play leaves you with exactly one card, set `"called_uno": true` on that
play. If you forget, the next seat to act may `challenge_uno` you before doing
anything else, and you draw 2 (the mercy rule applies). The window closes as
soon as the next seat acts. `called_uno` is only accepted on a play that leaves
you one card.

## Moves

Every move carries `"seat"` (your seat number) and `"kind"`. Card indexes refer
to your `hand` list as shown to you (0-based). Your `legal_moves` list is exact:
copy one of those.

Play a card:

```json
{"kind": "play", "seat": 2, "card_index": 3}
{"kind": "play", "seat": 2, "card_index": 0, "declared_colour": "red"}
{"kind": "play", "seat": 2, "card_index": 5, "target_seat": 0}
{"kind": "play", "seat": 2, "card_index": 1, "called_uno": true}
```

* `declared_colour` (red/yellow/green/blue): required for a wild, forbidden otherwise.
* `target_seat`: required when the card is a 7 (any other surviving seat), forbidden otherwise.
* `called_uno`: `true` only when this play leaves you with one card.

Draw (one card in phase `play`; the whole pending total in `draw_response`):

```json
{"kind": "draw", "seat": 2}
```

Pass, only in phase `after_draw` (you drew and cannot or do not want to play it):

```json
{"kind": "pass", "seat": 2}
```

Challenge a seat that sits on one card without having called UNO (only before
your own action; does not use up your turn):

```json
{"kind": "challenge_uno", "seat": 2, "target": 0}
```

## What you see (`table`)

`discard_top`, `active_colour`, `direction`, `pending_draw`, `uno_pending`
(the seat open to a challenge, or null), `draw_pile_count`,
`discard_pile_count`, `eliminated`, and after your own draw `drawn_card`.
`hand_counts` shows how many cards each seat holds. `phase` is `play`,
`after_draw` or `draw_response`.

## How to think

* **Hand size is the danger.** Draw penalties stack: a 10 on a 6 on a 4 is a
  20-card hit and 25 cards means elimination. Count `pending_draw` plus your
  hand before you choose to draw; if drawing would put you at 25, stacking
  anything is better, and if you cannot stack, hope the total was already
  small.
* **Keep a stack answer.** A `wild_draw10` or `wild_draw6` in hand is
  insurance: it answers any stack. A `draw2` only answers a `draw2`.
* **Send stacks where they hurt.** A big pending total aimed at a seat holding
  15+ cards can knock it out; a `wild_reverse_draw4` turns a stack around onto
  the seat that started it.
* **7 and 0 are hand-size trades.** Play a 7 when a rival has fewer cards than
  you and swap with them; avoid it when you are the one who is short. A 0
  hands your pile to the next seat in direction, so check who that is.
* **Discard All** dumps a whole colour: hold it until you have several cards of
  that colour, or use it to go out.
* **Skip Everyone** gives you a second play: pair it with a draw card or a
  Discard All.
* **Call UNO** (`called_uno: true`) on the play that leaves you one card, and
  challenge opponents who forget.
* Watch `hand_counts`: a seat on 1-2 cards needs a draw card or a skip aimed
  at it now.
