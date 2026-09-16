import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import { RoomsService } from "@/client"
import { Chat } from "@/components/Rooms/Chat"
import { FocusView } from "@/components/Rooms/FocusView"
import { GameTable } from "@/components/Rooms/GameTable"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { useRoomSocket } from "@/hooks/useRoomSocket"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/rooms/$roomId")({
  component: RoomDetail,
  head: () => ({
    meta: [{ title: "Room - botpit" }],
  }),
})

function RoomDetail() {
  const { roomId } = Route.useParams()
  const { user: currentUser } = useAuth()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  const roomQuery = useQuery({
    queryKey: ["rooms", roomId],
    queryFn: async () =>
      (await RoomsService.getRoom({ path: { room_id: roomId } })).data,
  })
  const room = roomQuery.data

  const { live, status } = useRoomSocket(roomId)

  // A rejected move is a transient, seat-visible signal (docs/events.md) --
  // surface it as a toast rather than a persistent banner, since it's about
  // one specific attempt, not the room's ongoing state.
  useEffect(() => {
    if (live.lastRejection) {
      toast.error(`Move rejected: ${live.lastRejection.reason}`)
    }
  }, [live.lastRejection])

  // GET /rooms/{id} is fetched once; per-seat kind/status/persona has no
  // live equivalent of its own, so without this a page open before someone
  // else joins keeps rendering every seat as it was at load time forever.
  useEffect(() => {
    if (live.seatsVersion > 0) {
      queryClient.invalidateQueries({ queryKey: ["rooms", roomId] })
    }
  }, [live.seatsVersion, queryClient, roomId])

  const [awaitingSlot, setAwaitingSlot] = useState(false)

  const startMutation = useMutation({
    mutationFn: () => RoomsService.startRoom({ path: { room_id: roomId } }),
    onError: (err) => {
      // A 429 here (`CapacityExceededError`, app/rooms/service.py) means
      // this room got enqueued on the global concurrent-game cap rather
      // than refused outright -- show the wait, don't toast an error.
      if (err instanceof AxiosError && err.response?.status === 429) {
        setAwaitingSlot(true)
        return
      }
      handleError.call(showErrorToast, err)
    },
    onSuccess: () => {
      setAwaitingSlot(false)
      queryClient.invalidateQueries({ queryKey: ["rooms", roomId] })
    },
  })

  const globalQueueQuery = useQuery({
    queryKey: ["room-queue-global", roomId],
    queryFn: () =>
      RoomsService.roomQueue({
        path: { room_id: roomId },
        query: { scope: "global" },
      }),
    enabled: awaitingSlot,
    refetchInterval: 5000,
    retry: false,
  })

  // The room itself flips to "in_progress" (via WS, `live.roomStatus`) once
  // `_promote_next_queued_room` actually starts it -- that is the real
  // signal this wait is over, not a client-side guess.
  useEffect(() => {
    if (awaitingSlot && live.roomStatus && live.roomStatus !== "waiting") {
      setAwaitingSlot(false)
    }
  }, [awaitingSlot, live.roomStatus])

  const forceStartMutation = useMutation({
    mutationFn: () =>
      RoomsService.forceStartRoom({ path: { room_id: roomId } }),
    onError: handleError.bind(showErrorToast),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["rooms", roomId] }),
  })

  const moveMutation = useMutation({
    mutationFn: (move: Record<string, unknown>) =>
      RoomsService.submitMove({ path: { room_id: roomId }, body: { move } }),
    onError: handleError.bind(showErrorToast),
  })

  const leaveMutation = useMutation({
    mutationFn: () => RoomsService.leaveRoom({ path: { room_id: roomId } }),
    onError: handleError.bind(showErrorToast),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["rooms", roomId] }),
  })

  if (roomQuery.isLoading || !room) {
    return <p className="text-muted-foreground">Loading room...</p>
  }

  const mySeat = room.seats.find((s) => s.user_id === currentUser?.id)
  const isCreator = room.creator_id === currentUser?.id
  const isPrivileged = isCreator || currentUser?.is_superuser === true
  const roomStatus = live.roomStatus ?? room.status
  const myView = mySeat ? live.viewsBySeat[mySeat.seat] : undefined

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-2xl">{room.game}</h1>
          <p className="text-sm text-muted-foreground">
            Code {room.code} &middot; {room.seats.length} seats
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={status === "open" ? "default" : "outline"}>
            {status === "open" ? "live" : status}
          </Badge>
          <Badge variant="outline">{roomStatus}</Badge>
        </div>
      </div>

      {roomStatus === "waiting" ? (
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
          <div className="flex flex-col gap-1">
            {room.seats.map((seat) => (
              <div key={seat.id} className="flex items-center gap-2 text-sm">
                <span className="w-16 text-muted-foreground">
                  Seat {seat.seat}
                </span>
                <Badge variant="outline">{seat.kind}</Badge>
                <span className="text-muted-foreground">{seat.status}</span>
                {seat.persona ? (
                  <Badge variant="outline">{seat.persona}</Badge>
                ) : null}
                {seat.user_id === currentUser?.id ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => leaveMutation.mutate()}
                    disabled={leaveMutation.isPending}
                  >
                    Leave seat
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
          {isPrivileged ? (
            <div className="flex flex-col gap-2">
              <div className="flex gap-2">
                <Button
                  onClick={() => startMutation.mutate()}
                  disabled={startMutation.isPending || awaitingSlot}
                >
                  Start
                </Button>
                <Button
                  variant="outline"
                  onClick={() => forceStartMutation.mutate()}
                  disabled={forceStartMutation.isPending}
                >
                  Force start (fill open seats with AI)
                </Button>
              </div>
              {awaitingSlot ? (
                <div
                  role="status"
                  aria-live="polite"
                  className="flex items-center gap-2 text-sm"
                >
                  <Badge variant="outline">Waiting for a game slot</Badge>
                  <span className="text-muted-foreground">
                    Position {globalQueueQuery.data?.data.position ?? "…"} of{" "}
                    {globalQueueQuery.data?.data.length ?? "…"} rooms in line
                    {globalQueueQuery.data?.data.eta_seconds != null
                      ? ` — ETA ~${Math.ceil(
                          globalQueueQuery.data.data.eta_seconds / 60,
                        )} min`
                      : ""}
                  </span>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {roomStatus === "in_progress" && myView ? (
        <GameTable
          view={myView}
          onSubmitMove={(move) => moveMutation.mutate(move)}
          submitting={moveMutation.isPending}
        />
      ) : null}

      {(roomStatus === "finished" || roomStatus === "stopped") &&
      !isPrivileged ? (
        <div className="rounded-lg border border-border bg-card p-6 text-center">
          <p className="font-display text-lg">
            {live.finished
              ? live.finished.winner_seat !== null
                ? `Seat ${live.finished.winner_seat} won`
                : "Game over"
              : "Room stopped"}
          </p>
        </div>
      ) : null}

      {isPrivileged && roomStatus !== "waiting" ? (
        <div className="flex flex-col gap-2 border-t border-border pt-6">
          <h2 className="font-display text-lg">Focus view</h2>
          <FocusView seats={room.seats} live={live} />
        </div>
      ) : null}

      <Chat
        roomId={roomId}
        chatLog={live.chatLog}
        seats={room.seats}
        mySeat={mySeat?.seat ?? null}
      />
    </div>
  )
}
