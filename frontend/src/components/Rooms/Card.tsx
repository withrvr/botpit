import {
  frenchRankLabel,
  frenchSuitGlyph,
  isFrenchCard,
  isRedSuit,
  isUnoCard,
  unoCardLabel,
  unoColourClasses,
  unoRankLabel,
} from "@/lib/cards"
import { cn } from "@/lib/utils"

interface CardProps {
  card: Record<string, unknown>
  className?: string
  /** Renders smaller, for an opponent's face-down count or a dense log. */
  size?: "sm" | "md"
}

/** One card face, drawn (not imaged) per the family the card dict's own
 * shape declares (backend/app/games/cards.py) -- an UNO-colour swatch with
 * its rank, or a cardstock face with a suit glyph, matching the app's
 * design tokens (docs/development/frontend-design.md). */
export function Card({ card, className, size = "md" }: CardProps) {
  const dims = size === "sm" ? "h-14 w-10 text-xs" : "h-20 w-14 text-sm"

  if (isUnoCard(card)) {
    return (
      <div
        role="img"
        aria-label={unoCardLabel(card)}
        className={cn(
          "flex flex-col items-center justify-center rounded-lg border border-border/60 font-semibold shadow-sm",
          dims,
          unoColourClasses(card.colour),
          className,
        )}
      >
        <span className="text-center leading-tight px-1">
          {unoRankLabel(card.rank)}
        </span>
      </div>
    )
  }

  if (isFrenchCard(card)) {
    const red = isRedSuit(card.suit)
    return (
      <div
        role="img"
        aria-label={`${frenchRankLabel(card.rank)} of ${card.suit}`}
        className={cn(
          "flex flex-col items-center justify-center rounded-lg border border-border bg-card font-semibold shadow-sm",
          dims,
          red ? "text-[#c1272d] dark:text-[#e6605a]" : "text-foreground",
          className,
        )}
      >
        <span className="leading-none">{frenchRankLabel(card.rank)}</span>
        <span className="text-lg leading-none">
          {frenchSuitGlyph(card.suit)}
        </span>
      </div>
    )
  }

  return (
    <div
      className={cn(
        "flex items-center justify-center rounded-lg border border-dashed border-border text-muted-foreground",
        dims,
        className,
      )}
    >
      ?
    </div>
  )
}
