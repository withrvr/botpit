# UNO Flip!

Two to ten seats. Every card has a **light** face and a **dark** face. The
game starts light; each Flip card turns the whole game over. You only see the
face currently up, even on your own cards. First seat to empty its hand wins.

## The deck (112 double-faced cards)

Light faces, per colour (red, yellow, green, blue): two each of 1-9 (no 0), two
Draw One (`draw1`), two Reverse (`reverse`), two Skip (`skip`), two Flip (`flip`);
plus four Wild (`wild`) and four Wild Draw Two (`wild_draw2`).

Dark faces, per colour (pink, teal, orange, purple): two each of 1-9, two Draw
Five (`draw5`), two Reverse (`reverse`), two Skip Everyone (`skip_all`), two Flip
(`flip`); plus four Wild (`wild`) and four Wild Draw Colour (`wild_draw_colour`).

Which light face backs which dark face is randomised per game (Mattel prints
a fixed pairing; the count of each face per side is identical).

## Matching

On your turn play one card whose **active face** matches the **active
colour**, or the rank of the discard top, or is a wild face. If the active
colour is `null` (wild face on top after a Flip), any card matches.

If you have no playable card you must draw one card (`draw`). You may then
play that card if it matches (`play` its index, which is always the last one in
your hand) or `pass`. You cannot draw while you hold a playable card.

## Light-side action cards

- **Draw One** (`draw1`): next player draws 1, loses their turn.
- **Skip** (`skip`): next player loses their turn.
- **Reverse** (`reverse`): direction reverses (two seats: you play again).
- **Flip** (`flip`): see below.
- **Wild** (`wild`): declare a light colour (red, yellow, green, blue).
- **Wild Draw Two** (`wild_draw2`): declare a light colour; next player draws
  2, loses their turn. Meant only when you hold no card of the active colour;
  bluffing is allowed but the victim may challenge (see Challenges).

## Dark-side action cards

- **Draw Five** (`draw5`): next player draws 5, loses their turn.
- **Skip Everyone** (`skip_all`): everyone else is skipped; you play again.
- **Reverse**, **Flip**: as on the light side.
- **Wild** (`wild`): declare a dark colour (pink, teal, orange, purple).
- **Wild Draw Colour** (`wild_draw_colour`): declare a dark colour; next player
  draws until they draw that colour, then loses their turn. Same legality and
  challenge rule as Wild Draw Two. Drawing stops if the cards run out.

## Flip

When a Flip card is played the side switches (light -> dark or dark -> light):

1. The discard pile is turned over as a block. The Flip card you just played
   is now at the bottom; the card that was at the bottom is now on top, showing
   its other face. The active colour becomes that card's colour (or `null` if
   it is a wild face, in which case the next player may play anything).
2. The draw pile and every hand are turned over: the same physical cards, now
   showing their other faces. Nobody draws or discards anything.
3. The next player must match the new top card. Its action, if any, does
   **not** trigger; only the Flip itself took effect.

Effects of a card played after a flip use the new side's rules, and wilds
declare colours of the new side.

## UNO

Set `"called_uno": true` on the play that leaves you one card. If you forget,
the next seat may `challenge_uno` **before doing anything else**: you draw 2
and the catcher keeps their turn. Any other move by that seat closes the
window. `called_uno` is rejected unless the play leaves exactly one card.

## Challenges (Wild Draw Two / Wild Draw Colour)

The targeted player must respond with `accept_wild_draw` (take the penalty,
lose the turn) or `challenge_wild_draw`. The engine checks whether the player
held the colour that was active when they played the card:

- Wild Draw Two, played illegally (they held a matching colour): they draw 2;
  the challenger keeps their turn and plays next with the declared colour.
- Wild Draw Two, played legally: the challenger draws 4 and loses their turn.
- Wild Draw Colour, illegal: the player who played it draws until the declared
  colour; the challenger keeps their turn.
- Wild Draw Colour, legal: the challenger draws until the declared colour and
  then 2 more, and loses their turn.

Open colour (`null`) at play time means legal. A winning wild draw card ends
the round; the next player takes the penalty with no challenge.

## Start of the round

Seven cards each, light side up. The first draw-pile card starts the discard
pile; an action card acts on seat 0 (Draw One: seat 0 draws 1 and is skipped;
Skip; Reverse: last seat plays first; Flip: game flips before seat 0 acts;
Wild: colour open). A Wild Draw Two first card is reshuffled back.

## Draw pile, end of round, scoring

An empty draw pile is refilled by shuffling the discard pile except its top.
If a player has no playable card and nothing to draw, the round ends with no
winner and seats rank by hand points.

The round ends when a seat plays its last card. Hand points count the face
showing at the end: numbers face value, Draw One 10, Draw Five / Reverse / Skip / Flip
20, Skip Everyone 30, Wild 40, Wild Draw Two 50, Wild Draw Colour 60. Ranking:
the winner first, then ascending hand points, ties by seat number. Lower is
better for everyone but the winner.

## What you see

`hand` lists the **active face** of each of your cards with its `index`; the
hidden faces are unknown to everyone. `table` shows `side`, `discard_top`,
`active_colour` (`null` = open), `direction`, `pending_draw` (`2` or
`"colour:<colour>"` while a wild draw awaits your response, else `null`),
`uno_pending` (a seat that can be caught), `draw_pile_count`,
`discard_pile_count`, `winner`, and, right after you draw, `drawn_card`.
`legal_moves` lists exactly what you may do now.

**Output contract:** reply with exactly one JSON object, one of the kinds below, and it must be one of the entries in `legal_moves`.

## Moves (JSON)

Every move carries your `seat`. Indexes are 0-based positions in your `hand`.

```json
{"kind": "play", "seat": 2, "card_index": 3}
{"kind": "play", "seat": 2, "card_index": 3, "called_uno": true}
{"kind": "play", "seat": 2, "card_index": 0, "declared_colour": "teal"}
{"kind": "play", "seat": 2, "card_index": 0, "card": {"colour": null, "rank": "wild_draw_colour"}, "declared_colour": "pink"}
{"kind": "draw", "seat": 2}
{"kind": "pass", "seat": 2}
{"kind": "challenge_uno", "seat": 2, "target": 1}
{"kind": "accept_wild_draw", "seat": 2}
{"kind": "challenge_wild_draw", "seat": 2}
```

- `declared_colour` is required for a wild face and must belong to the current
  side; it is forbidden on any other card.
- `card` is optional: state the face you mean to play and the engine rejects
  the move if `card_index` points at a different card.
- After a `draw`, `play` is only accepted for the drawn card (last index).

## How to think

1. Check `table.side` first: it decides colours, faces and end-of-game costs.
2. Match `active_colour` or the top rank; keep wilds for when you are stuck.
3. Flip when the current side is going badly for you; dark-side cards punish
   hard, so flip back to light when opponents are nearly out.
4. Set `called_uno` when going down to one card; catch opponents who forget.
5. Challenge a wild draw only if you believe the player held the active
   colour; a failed challenge costs more than accepting.
