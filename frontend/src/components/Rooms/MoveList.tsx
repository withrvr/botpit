import { useState } from "react"

import { Button } from "@/components/ui/button"
import { humanizeMoveKind } from "@/lib/cards"
import { Card } from "./Card"

interface MoveListProps {
  legalMoves: Record<string, unknown>[]
  hand: Record<string, unknown>[]
  onSubmit: (move: Record<string, unknown>) => void
  submitting: boolean
}

function moveLabel(move: Record<string, unknown>): string {
  const kind = typeof move.kind === "string" ? move.kind : "move"
  if (kind === "play" && move.declared_colour) {
    return `${humanizeMoveKind(kind)} — declare ${move.declared_colour}`
  }
  if (kind === "challenge_uno" && typeof move.target === "number") {
    return `${humanizeMoveKind(kind)} seat ${move.target}`
  }
  return humanizeMoveKind(kind)
}

/**
 * Renders the current seat's `legal_moves` as clickable options -- every
 * botpit game (each game's own moves.py under backend/app/games/)
 * enumerates each fully legal move as its own entry, wild-colour
 * declarations included, so there is
 * no client-side move-building UI needed for the common case: pick one,
 * submit it verbatim.
 *
 * The one exception is Hearts' `pass_cards` phase, where `legal_moves`
 * enumerates every 3-card combination from hand (up to 286) -- rendering
 * those as buttons would be absurd, so that one case gets a real
 * multi-select-then-confirm UI instead, detected generically by move kind
 * rather than by game slug.
 */
export function MoveList({
  legalMoves,
  hand,
  onSubmit,
  submitting,
}: MoveListProps) {
  const [selected, setSelected] = useState<number[]>([])

  if (legalMoves.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">Waiting for your turn...</p>
    )
  }

  const isPassCards = legalMoves[0]?.kind === "pass_cards"

  if (isPassCards) {
    const toggle = (index: number) => {
      setSelected((prev) =>
        prev.includes(index)
          ? prev.filter((i) => i !== index)
          : prev.length < 3
            ? [...prev, index]
            : prev,
      )
    }
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Choose 3 cards to pass ({selected.length}/3 selected)
        </p>
        <div className="flex flex-wrap gap-2">
          {hand.map((card, index) => (
            <button
              key={index}
              type="button"
              onClick={() => toggle(index)}
              aria-pressed={selected.includes(index)}
              className="rounded-lg outline-offset-2 ring-offset-2 focus-visible:outline-2 focus-visible:outline-ring aria-pressed:ring-2 aria-pressed:ring-accent"
            >
              <Card card={card} />
            </button>
          ))}
        </div>
        <Button
          disabled={selected.length !== 3 || submitting}
          onClick={() =>
            onSubmit({
              kind: "pass_cards",
              cards: [...selected].sort((a, b) => a - b),
            })
          }
        >
          Pass selected cards
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-wrap gap-2">
      {legalMoves.map((move, index) => (
        <Button
          key={index}
          variant="outline"
          disabled={submitting}
          onClick={() => onSubmit(move)}
          className="h-auto gap-2 py-2"
        >
          {move.card && typeof move.card === "object" ? (
            <Card
              card={move.card as Record<string, unknown>}
              size="sm"
              className="border-0 shadow-none"
            />
          ) : null}
          {moveLabel(move)}
        </Button>
      ))}
    </div>
  )
}
