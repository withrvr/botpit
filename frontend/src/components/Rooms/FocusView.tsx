import { useState } from "react"
import type { RoomSeatPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { RoomLiveState } from "@/lib/roomLiveState"
import { Card } from "./Card"

interface FocusViewProps {
  seats: RoomSeatPublic[]
  live: RoomLiveState
}

/**
 * The privileged spectator/admin view (DECISIONS.md §10): click any seat
 * and see its hand, current `player_view`, model/persona, and live AI
 * reasoning -- ordered so reasoning streams in before the move it explains
 * lands, which falls out naturally here since `agent.reasoning` publishes
 * before the `game.move_applied`/`game.view_updated` pair it precedes
 * (backend/app/agents' turn sequencing), and this component just renders
 * whatever `RoomLiveState` currently holds for the selected seat.
 *
 * Only ever reachable with data: `viewsBySeat`/`reasoningBySeat` are
 * populated for every seat only when the subscriber is privileged
 * (server-side `filter_for_subscriber`) -- a non-privileged caller's
 * `live` simply never has more than its own seat's entry, so this
 * component is safe to render for anyone in principle, it just won't
 * have anything to show beyond their own seat.
 */
export function FocusView({ seats, live }: FocusViewProps) {
  const [selected, setSelected] = useState(0)
  const seat = seats.find((s) => s.seat === selected)
  const view = live.viewsBySeat[selected]
  const reasoning = live.reasoningBySeat[selected]

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap gap-2">
        {seats.map((s) => (
          <Button
            key={s.seat}
            size="sm"
            variant={s.seat === selected ? "default" : "outline"}
            onClick={() => setSelected(s.seat)}
          >
            Seat {s.seat}
            {s.kind === "agent" ? " (AI)" : ""}
          </Button>
        ))}
      </div>

      {seat ? (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{seat.kind}</Badge>
          {seat.model ? <Badge variant="outline">{seat.model}</Badge> : null}
          {seat.persona ? (
            <Badge variant="outline">{seat.persona}</Badge>
          ) : null}
        </div>
      ) : null}

      {view ? (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <span className="text-sm text-muted-foreground">
              Seat {selected}'s hand
            </span>
            <div className="flex flex-wrap gap-2">
              {view.hand.map((card, index) => (
                <Card key={`${JSON.stringify(card)}-${index}`} card={card} />
              ))}
            </div>
          </div>
          <div className="text-sm text-muted-foreground">
            Phase: {view.phase} &middot; Current turn: seat {view.current_seat}
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          No view for this seat yet -- it will appear once the game starts.
        </p>
      )}

      {reasoning ? (
        // Keyed on ts so each new reasoning update re-triggers the reveal --
        // this is the one moment DECISIONS.md §10 calls out specifically
        // ("reasoning streams before the move it explains lands"), worth a
        // deliberate entrance rather than an instant swap.
        <div
          key={reasoning.ts}
          className="flex flex-col gap-1 rounded-lg border border-border bg-card p-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-500"
        >
          <span className="text-xs text-muted-foreground">
            {reasoning.model} is thinking...
          </span>
          <p className="whitespace-pre-wrap text-sm">{reasoning.text}</p>
        </div>
      ) : null}
    </div>
  )
}
