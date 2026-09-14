import { useMutation } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import type { RoomSeatPublic } from "@/client"
import { RoomsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import useCustomToast from "@/hooks/useCustomToast"
import type { ChatEntry } from "@/lib/roomLiveState"
import { handleError } from "@/utils"

interface ChatProps {
  roomId: string
  chatLog: ChatEntry[]
  seats: RoomSeatPublic[]
  /** No seat, no send box -- matches the backend's own own-seat-only rule
   * (DECISIONS.md §8 is "players," not spectators) rather than letting a
   * spectator submit and hit a confusing 403. */
  mySeat: number | null
}

const MAX_CHAT_LENGTH = 200

/** Public chat, shared between human and AI seats (DECISIONS.md §8) --
 * downstream-only, never affects game state. Every seat's messages render
 * the same way regardless of kind; the seat badge (human/AI + persona)
 * is what makes an AI's voice legible as a distinct character rather
 * than an anonymous "seat 2". */
export function Chat({ roomId, chatLog, seats, mySeat }: ChatProps) {
  const [draft, setDraft] = useState("")
  const { showErrorToast } = useCustomToast()

  // Same bug class fixed once already in useRoomSocket: without this, a
  // half-typed message survives a room switch and can get sent into the
  // wrong room. roomId isn't read in the body -- it's a reset trigger, not
  // a data dependency.
  // biome-ignore lint/correctness/useExhaustiveDependencies: roomId is the reset trigger, not a body dependency
  useEffect(() => {
    setDraft("")
  }, [roomId])

  const mutation = useMutation({
    mutationFn: (text: string) =>
      RoomsService.sendChat({ path: { room_id: roomId }, body: { text } }),
    onSuccess: () => setDraft(""),
    onError: handleError.bind(showErrorToast),
  })

  const seatLabel = (seat: number) => {
    const s = seats.find((seat_) => seat_.seat === seat)
    return s?.kind === "agent" ? (s.persona ?? "AI") : `Seat ${seat}`
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <h2 className="font-display text-lg">Chat</h2>
      <div
        role="log"
        aria-live="polite"
        className="flex max-h-64 flex-col gap-2 overflow-y-auto"
      >
        {chatLog.length === 0 ? (
          <p className="text-sm text-muted-foreground">No messages yet.</p>
        ) : (
          chatLog.map((entry) => (
            <div key={entry.eventId} className="flex items-start gap-2 text-sm">
              <Badge variant="outline" className="shrink-0">
                {seatLabel(entry.seat)}
              </Badge>
              <span className="break-words">{entry.text}</span>
            </div>
          ))
        )}
      </div>
      {mySeat !== null ? (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            const text = draft.trim()
            if (text) mutation.mutate(text)
          }}
        >
          <Input
            aria-label="Chat message"
            placeholder="Say something..."
            value={draft}
            maxLength={MAX_CHAT_LENGTH}
            onChange={(e) => setDraft(e.target.value)}
          />
          <Button type="submit" disabled={mutation.isPending || !draft.trim()}>
            Send
          </Button>
        </form>
      ) : (
        <p className="text-xs text-muted-foreground">
          Only seated players can chat.
        </p>
      )}
    </div>
  )
}
