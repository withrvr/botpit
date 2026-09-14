/**
 * Card-face helpers for the two card families every botpit game deals in
 * (backend/app/games/cards.py): UNO-style (`colour`/`rank`) across the
 * three UNO variants, and French-deck (`suit`/`rank`) for Hearts. A card
 * dict's own shape says which family it is -- no game-slug branching
 * needed here or in the components that use these.
 */

export type UnoCardData = Record<string, unknown> & {
  colour: string | null
  rank: string
}

export type FrenchCardData = Record<string, unknown> & {
  suit: "clubs" | "diamonds" | "hearts" | "spades"
  rank: number
}

export function isUnoCard(card: Record<string, unknown>): card is UnoCardData {
  return "colour" in card && "rank" in card
}

export function isFrenchCard(
  card: Record<string, unknown>,
): card is FrenchCardData {
  return "suit" in card && "rank" in card
}

/** Tailwind text/bg color classes for each UNO colour, tied to the app's
 * own token set where the family overlaps it (red/accent), literal
 * swatches otherwise -- there is no themed token for "UNO yellow." */
const UNO_COLOUR_CLASSES: Record<string, string> = {
  red: "bg-[#c1272d] text-[#f1eee6] dark:bg-[#e6605a] dark:text-[#14181f]",
  yellow: "bg-[#e0b400] text-[#14181f]",
  green: "bg-[#1f4a44] text-[#f1eee6] dark:bg-[#2f6e64] dark:text-[#f1eee6]",
  blue: "bg-[#22558f] text-[#f1eee6]",
  pink: "bg-[#c94f8a] text-[#14181f]",
  teal: "bg-[#1f7a72] text-[#f1eee6]",
  orange: "bg-[#c9702e] text-[#14181f]",
  purple: "bg-[#5c3d8f] text-[#f1eee6]",
}

const UNO_WILD_CLASSES = "bg-[#14181f] text-[#f1eee6] dark:bg-[#1b2028]"

export function unoColourClasses(colour: string | null): string {
  if (colour === null) return UNO_WILD_CLASSES
  return UNO_COLOUR_CLASSES[colour] ?? UNO_WILD_CLASSES
}

const UNO_RANK_LABELS: Record<string, string> = {
  skip: "Skip",
  reverse: "Reverse",
  draw2: "+2",
  wild: "Wild",
  wild_draw4: "Wild +4",
  flip: "Flip",
  draw_color: "+ color",
  discard_all: "Discard all",
  skip_everyone: "Skip all",
}

export function unoRankLabel(rank: string): string {
  return (
    UNO_RANK_LABELS[rank] ?? (rank.length <= 2 ? rank : rank.replace(/_/g, " "))
  )
}

export function unoCardLabel(card: UnoCardData): string {
  const rank = unoRankLabel(card.rank)
  return card.colour === null ? rank : `${card.colour} ${rank}`
}

const SUIT_GLYPHS: Record<FrenchCardData["suit"], string> = {
  clubs: "♣",
  diamonds: "♦",
  hearts: "♥",
  spades: "♠",
}

const RANK_NAMES: Record<number, string> = {
  11: "J",
  12: "Q",
  13: "K",
  14: "A",
}

export function frenchSuitGlyph(suit: FrenchCardData["suit"]): string {
  return SUIT_GLYPHS[suit]
}

export function frenchRankLabel(rank: number): string {
  return RANK_NAMES[rank] ?? String(rank)
}

export function frenchCardLabel(card: FrenchCardData): string {
  return `${frenchRankLabel(card.rank)} of ${card.suit}`
}

export function isRedSuit(suit: FrenchCardData["suit"]): boolean {
  return suit === "hearts" || suit === "diamonds"
}

/** Humanizes a move's `kind` for anything without its own bespoke label
 * (draw, pass, challenges) -- "wild_draw4" -> "Wild draw4", "draw" ->
 * "Draw". Good enough for every current move kind across all four games;
 * a genuinely awkward one can get its own entry here later. */
export function humanizeMoveKind(kind: string): string {
  const spaced = kind.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
