# Classic UNO

Official Mattel rules, one round, 2-10 seats. First seat to empty its hand wins.
Seats are numbered `0..seat_count-1`; play moves to `seat + direction` (wrapping).

## Cards

Four colours: `red`, `yellow`, `green`, `blue`. A card is `{"colour": ..., "rank": ...}`;
wilds have `"colour": null`.

| rank | per colour | effect | points |
|---|---|---|---|
| `"0"` | 1 | none | 0 |
| `"1"`..`"9"` | 2 each | none | face value |
| `"skip"` | 2 | next player loses their turn | 20 |
| `"reverse"` | 2 | direction flips (with 2 seats: acts as a Skip) | 20 |
| `"draw2"` | 2 | next player draws 2 and loses their turn | 20 |
| `"wild"` (colour null) | 4 total | you choose the active colour | 50 |
| `"wild_draw4"` (colour null) | 4 total | you choose the colour; next player draws 4 and loses their turn | 50 |

108 cards. Everyone is dealt 7. The top card of the draw pile starts the discard pile:
a number starts normal play; a Skip / Reverse / Draw Two applies to seat 0 as if
the dealer had played it; a Wild lets seat 0 play any card (the colour is fixed by
that play); a Wild Draw Four is shuffled back and another card is turned.

## Matching

You may play a card that matches the discard top by **colour** (the active colour,
which for a wild on top is the colour its player declared), by **number**, or by
**symbol** (Skip on Skip, Draw Two on Draw Two, ...). Wilds can always be played.
No stacking: a Draw Two or Wild Draw Four penalty is taken, never passed on.

## Your turn

Each turn you do exactly one of:

1. **Play** one matching card from your hand.
2. **Draw** one card. If the drawn card is playable you may play it immediately
   (only that card), or **pass**. If it is not playable you must pass.

There is no other way to pass: you cannot pass while holding a playable card
unless you drew first.

## Wild Draw Four and the challenge

You may play a Wild Draw Four **only if you hold no card of the active colour**
(other wilds and rank matches do not matter). The engine lets you play it anyway,
but the next player may **challenge** before drawing:

- Challenge and the play was illegal: the player who played it draws 4 and the
  challenger takes a normal turn (the declared colour stays).
- Challenge and the play was legal: the challenger draws 6 and loses their turn.
- Accept: the challenger draws 4 and loses their turn.

While a Wild Draw Four is pending, the victim's only moves are `accept_draw4` and
`challenge_draw4`.

## Saying UNO

When a play leaves you with **one** card you must call UNO on that play
(`"called_uno": true`). If you forget, the next seat to take a turn may catch you
with `challenge_uno` before their own action: you draw 2. Catching someone does not
use up the catcher's turn. The window closes as soon as any seat plays, draws or
passes. `called_uno` is only accepted on the play that leaves exactly one card.

## Draw pile

When the draw pile runs out, the discard pile except its top card is shuffled into a
new draw pile. If neither pile can supply a card, a draw simply stops short. If a
player starts their turn with no playable card and no card can be drawn, the game
ends immediately with no winner and everyone is ranked by points in hand.

## End and scoring

The game ends the moment a hand is empty (a final Draw Two / Wild Draw Four still
makes the next player draw, without a challenge). Each seat's score is the points
left in its hand (see the table). Ranking: winner first, then ascending points, ties
by seat number.

## Move vocabulary

Every move carries `"seat"` (your seat number). Copy a move from `legal_moves` verbatim when possible: it already contains the right `card` echo and colour.

**Output contract:** reply with exactly one JSON object, one of the kinds below, and it must be one of the entries in `legal_moves`.

```json
{"kind": "play", "seat": 2, "card_index": 3, "declared_colour": null, "called_uno": false}
{"kind": "play", "seat": 2, "card_index": 0, "declared_colour": "blue", "called_uno": true}
{"kind": "draw", "seat": 2}
{"kind": "pass", "seat": 2}
{"kind": "challenge_uno", "seat": 2, "target": 1}
{"kind": "challenge_draw4", "seat": 2}
{"kind": "accept_draw4", "seat": 2}
```

- `card_index` is the position in your `hand` list (0-based).
- `declared_colour` is required for `wild` and `wild_draw4` (`"red"`, `"yellow"`,
  `"green"`, `"blue"`) and must be omitted or `null` for coloured cards.
- `called_uno` is only legal when the play leaves you exactly one card.
- `card` may be included to echo the card being played; it must match the hand.

## What you see

`phase` is `"play"` (normal turn), `"after_draw"` (you just drew; play the drawn
card or pass) or `"draw4_response"` (accept or challenge). `table` holds
`discard_top`, `active_colour`, `direction`, `pending_draw` (4 while a Wild Draw
Four awaits your answer), `uno_pending` (seats with one card who did not call),
`draw_pile_count`, `discard_pile_count` and, only in your own `after_draw` view,
`drawn_card`.

## How to think

- Check `uno_pending` first: catching someone is free and costs them 2 cards.
- Prefer playing over drawing; prefer action cards when the next player is low on
  cards, and keep wilds for when you have no match.
- Choose a wild colour you hold the most of.
- A Wild Draw Four is a bluff if you hold the active colour; the victim will often
  challenge when you have many cards. Challenge when the player is likely to have
  held a match (large hand, colour just changed to something common).
- Say UNO (`called_uno: true`) every time a play leaves you one card, or you will
  be caught.
