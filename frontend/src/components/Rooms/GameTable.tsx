import { Badge } from "@/components/ui/badge"
import type { PlayerViewPayload } from "@/lib/events"
import { Card } from "./Card"
import { MoveList } from "./MoveList"

interface GameTableProps {
  view: PlayerViewPayload
  onSubmitMove: (move: Record<string, unknown>) => void
  submitting: boolean
}

/** The shared table state (discard pile, current trick, direction, ...) is
 * game-specific and arbitrary (`PlayerView.table`, backend/app/games/base.py)
 * -- rendered generically here as labeled cards/values rather than a
 * bespoke layout per game, since all four games' table shapes are small
 * enough that a direct dump reads fine once each field gets a card face
 * where the value looks like one. */
function TableState({ table }: { table: Record<string, unknown> }) {
  const entries = Object.entries(table)
  if (entries.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-4">
      {entries.map(([key, value]) => (
        <div key={key} className="flex flex-col items-center gap-1">
          <span className="text-xs text-muted-foreground capitalize">
            {key.replace(/_/g, " ")}
          </span>
          {value && typeof value === "object" && !Array.isArray(value) ? (
            <Card card={value as Record<string, unknown>} />
          ) : (
            <span className="font-display text-lg">{String(value)}</span>
          )}
        </div>
      ))}
    </div>
  )
}

export function GameTable({ view, onSubmitMove, submitting }: GameTableProps) {
  const isMyTurn = view.current_seat === view.seat

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {/* Keyed on current_seat so each new turn re-triggers the reveal --
            the one moment on this page worth calling out, not a hover
            effect on every element (docs/development/frontend-design.md). */}
        <h2
          key={view.current_seat}
          className="font-display text-xl motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-top-2 motion-safe:duration-300"
        >
          {isMyTurn ? "Your turn" : `Waiting on seat ${view.current_seat}`}
        </h2>
        <div className="flex gap-2">
          {view.hand_counts.map((count, seat) => (
            <Badge
              key={seat}
              variant={seat === view.current_seat ? "default" : "outline"}
            >
              Seat {seat}: {count}
            </Badge>
          ))}
        </div>
      </div>

      <TableState table={view.table} />

      <div className="flex flex-col gap-2">
        <span className="text-sm text-muted-foreground">Your hand</span>
        <div className="flex flex-wrap gap-2">
          {view.hand.map((card, index) => (
            <Card key={index} card={card} />
          ))}
        </div>
      </div>

      {isMyTurn ? (
        <MoveList
          legalMoves={view.legal_moves}
          hand={view.hand}
          onSubmit={onSubmitMove}
          submitting={submitting}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          Waiting for seat {view.current_seat}...
        </p>
      )}
    </div>
  )
}
