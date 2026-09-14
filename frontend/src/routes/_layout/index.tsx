import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Suspense } from "react"

import { RoomsService } from "@/client"
import { CreateRoomDialog } from "@/components/Rooms/CreateRoomDialog"
import { JoinByCode } from "@/components/Rooms/JoinByCode"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"

export const Route = createFileRoute("/_layout/")({
  component: Lobby,
  head: () => ({
    meta: [{ title: "Lobby - botpit" }],
  }),
})

function getRoomsQueryOptions() {
  return {
    queryFn: async () =>
      (await RoomsService.listRooms({ query: { skip: 0, limit: 100 } })).data,
    queryKey: ["rooms"],
  }
}

function RoomsListContent() {
  const { data: rooms } = useSuspenseQuery(getRoomsQueryOptions())

  if (!rooms || rooms.data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border py-16 text-center">
        <p className="font-display text-lg">No public rooms right now</p>
        <p className="text-sm text-muted-foreground">
          Create one, or join a private room with a code.
        </p>
      </div>
    )
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {rooms.data.map((room) => (
        <Link
          key={room.id}
          to="/rooms/$roomId"
          params={{ roomId: room.id }}
          className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary"
        >
          <div className="flex items-center justify-between">
            <span className="font-display text-lg">{room.game}</span>
            <Badge variant={room.status === "waiting" ? "outline" : "default"}>
              {room.status}
            </Badge>
          </div>
          <span className="text-sm text-muted-foreground">
            {room.seats.length} seat{room.seats.length === 1 ? "" : "s"}
          </span>
        </Link>
      ))}
    </div>
  )
}

function RoomsListSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={i} className="h-24 rounded-lg" />
      ))}
    </div>
  )
}

function Lobby() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl">The lobby</h1>
          <p className="text-muted-foreground">
            Join a public room, create your own, or use a join code.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <JoinByCode />
          <CreateRoomDialog />
        </div>
      </div>
      <Suspense fallback={<RoomsListSkeleton />}>
        <RoomsListContent />
      </Suspense>
    </div>
  )
}
