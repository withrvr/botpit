# UNO Flip!

Two to ten seats. Every card has two faces: a **light** face and a **dark**
face. The game starts on the light side; every Flip card played turns the
whole game over to the other side. You only ever see the face that is
currently up, on every card, including your own hand. The first seat to empty
its hand wins the round.

## The deck (112 double-faced cards)

Light faces, per colour (red, yellow, green, blue): two each of 1-9 (no 0), two
Draw One (`draw1`), two Reverse (`reverse`), two Skip (`skip`), two Flip (`flip`);
plus four Wild (`wild`) and four Wild Draw Two (`wild_draw2`).

Dark faces, per colour (pink, teal, orange, purple): two each of 1-9, two Draw
Five (`draw5`), two Reverse (`reverse`), two Skip Everyone (`skip_all`), two Flip
(`flip`); plus four Wild (`wild`) and four Wild Draw Colour (`wild_draw_colour`).

Which light face sits on the back of which dark face is randomised per game
(Mattel prints a fixed pairing; the number of each face per side is the same).
You cannot know the hidden face of any card, not even your own.

## Matching

On your turn play one card from your hand whose **active face** (the face for the
current side) either matches the **active colour**, or has the same rank as
the top of the discard pile, or is a wild face. If the active colour is `null`
(a wild face is on top after a Flip and no colour was declared), any card
matches.

If you have no playable card you must draw one card (`draw`). You may then
play that card if it matches (`play` its index, which is always the last one in
your hand) or `pass`. You cannot draw while you hold a playable card.

## Light-side action cards

- **Draw One** (`draw1`): the next player draws 1 and loses their turn.
- **Skip** (`skip`): the next player loses their turn.
- **Reverse** (`reverse`): play direction reverses. With two seats it acts as a
  Skip: you play again.
- **Flip** (`flip`): see below.
- **Wild** (`wild`): declare one of the light colours (red, yellow, green, blue).
- **Wild Draw Two** (`wild_draw2`): declare a light colour; the next player
  draws 2 and loses their turn. You are only supposed to play it when you hold
  no card matching the active colour; the engine lets you bluff, but the
  victim may challenge (see Challenges).

## Dark-side action cards

- **Draw Five** (`draw5`): the next player draws 5 and loses their turn.
- **Skip Everyone** (`skip_all`): every other seat is skipped; you play again
  immediately (with two seats it is simply another turn for you).
- **Reverse** (`reverse`): as on the light side.
- **Flip** (`flip`): see below.
- **Wild** (`wild`): declare one of the dark colours (pink, teal, orange, purple).
- **Wild Draw Colour** (`wild_draw_colour`): declare a dark colour; the next
  player draws from the draw pile until they draw a card of that colour, then
  loses their turn. Same legality and challenge rule as Wild Draw Two. If the
  draw pile and the reshuffled discard pile run out before the colour shows,
  the drawing stops.

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

When you play your second-to-last card, set `"called_uno": true` on that
`play`. If you do not, the seat whose turn comes next may catch you with
`challenge_uno` **before doing anything else**: you draw 2 cards, and the
catcher then continues their turn normally (catching does not use up their
turn). Once that seat plays, draws, passes, accepts or challenges a wild
draw, the window is closed. `called_uno` is only accepted on a play that
leaves you with exactly one card.

## Challenges (Wild Draw Two / Wild Draw Colour)

After one of these is played, the targeted player must respond, with either
`accept_wild_draw` (draw the penalty and lose the turn) or
`challenge_wild_draw`. The engine checks the hand of the player who played the
card against the colour that was active when they played it:

- Wild Draw Two, played illegally (they held a matching colour): they draw 2;
  the challenger keeps their turn and plays next with the declared colour.
- Wild Draw Two, played legally: the challenger draws 4 and loses their turn.
- Wild Draw Colour, illegal: the player who played it draws until the declared
  colour; the challenger keeps their turn.
- Wild Draw Colour, legal: the challenger draws until the declared colour and
  then 2 more, and loses their turn.

If the active colour was open (`null`) when the card was played, it was legal.
If a Wild Draw card is the winning (last) card, the next player takes the
penalty and the round ends; there is nothing to challenge.

## Start of the round

Seven cards each, light side up. The first card of the draw pile starts the
discard pile. If it is an action card it acts on seat 0: Draw One (seat 0
draws 1 and is skipped), Skip (seat 0 is skipped), Reverse (direction becomes
-1 and the last seat plays first), Flip (the game flips before seat 0 acts and
seat 0 matches the dark face), Wild (colour is open; seat 0 may play anything).
A Wild Draw Two first card goes back into the pile, which is reshuffled.

## Draw pile, end of round, scoring

When the draw pile is empty the discard pile except its top card is shuffled
into a new draw pile. If a player has no playable card and there is nothing
left to draw, the round ends with nobody out; seats are ranked by hand points.

The round ends when a seat plays its last card (winner) or by the rule above.
Points still in each hand are counted on the face that is showing when the
round ends: numbers face value, Draw One 10, Draw Five / Reverse / Skip / Flip
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
`legal_moves` lists exactly what you may do now; pick one of them.

(Engine note: because hands hold double-faced cards while views expose only
the active face, the generic contract privacy check does not apply to this
game; `tests/unit/games/test_uno_flip.py` carries an equivalent Flip-specific
check instead.)

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

1. Check `table.side` first: it decides which colours exist, what every face
   means and how much a card costs you at the end.
2. Match `active_colour` or the top rank; keep wilds for when you are stuck.
3. A Flip is strongest when the light side is going badly for you and, since
   nobody knows their hidden faces, it is a gamble for everyone; on the dark
   side Draw Five, Skip Everyone and Wild Draw Colour punish hard, so try to
   flip back to light when opponents are close to going out.
4. Always set `called_uno` when going down to one card; catch opponents who
   forget.
5. Challenge a Wild Draw Two / Wild Draw Colour only if you believe the player
   held the active colour; a failed challenge costs more than accepting.
6. On the dark side, count points: a hand full of 20-60 point faces is
   expensive if someone else goes out.
