import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useEffect, useState } from "react"

import { RoomsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

function formatEta(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined) return null
  const minutes = Math.ceil(seconds / 60)
  return minutes <= 1 ? "~1 min" : `~${minutes} min`
}

export function JoinByCode() {
  const [code, setCode] = useState("")
  const [queued, setQueued] = useState<{ roomId: string; code: string } | null>(
    null,
  )
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  const joinMutation = useMutation({
    mutationFn: (joinCode: string) =>
      RoomsService.joinRoom({ body: { code: joinCode } }),
    onSuccess: (res) => {
      if (res.data) {
        setQueued(null)
        navigate({ to: "/rooms/$roomId", params: { roomId: res.data.room_id } })
      }
    },
    onError: (err, joinCode) => {
      // A 409 with a `room_id` body means "no open seat, you've been
      // enqueued" (see `RoomFullError` in app/api/routes/rooms.py) -- that's
      // an expected state to show inline, not an error to toast.
      if (err instanceof AxiosError && err.response?.status === 409) {
        const roomId = (err.response.data as { room_id?: string } | undefined)
          ?.room_id
        if (roomId) {
          setQueued({ roomId, code: joinCode })
          return
        }
      }
      handleError.call(showErrorToast, err)
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["rooms"] })
    },
  })

  const queueQuery = useQuery({
    queryKey: ["room-queue", queued?.roomId],
    queryFn: () =>
      RoomsService.roomQueue({
        path: { room_id: queued?.roomId ?? "" },
        query: { scope: "seat" },
      }),
    enabled: queued !== null,
    refetchInterval: 3000,
    retry: false,
  })

  // A 404 means the queue itself let go of us (dropped after too many
  // missed offers) -- nothing left to poll or accept.
  useEffect(() => {
    if (
      queued &&
      queueQuery.error instanceof AxiosError &&
      queueQuery.error.response?.status === 404
    ) {
      setQueued(null)
    }
  }, [queued, queueQuery.error])

  const status = queueQuery.data?.data
  const eta = formatEta(status?.eta_seconds)

  return (
    <div className="flex flex-col gap-2">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (code.trim()) joinMutation.mutate(code.trim())
        }}
      >
        <Input
          aria-label="Join code"
          placeholder="Join code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="max-w-40"
          disabled={queued !== null}
        />
        <Button
          type="submit"
          variant="outline"
          disabled={joinMutation.isPending || queued !== null}
        >
          Join
        </Button>
      </form>

      {queued ? (
        <div
          role="status"
          aria-live="polite"
          className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3 text-sm"
        >
          {status?.offered ? (
            <>
              <div className="flex items-center gap-2">
                <Badge>Seat ready</Badge>
                <span>
                  A seat opened up
                  {status.offer_expires_in_seconds != null
                    ? ` — accept within ${status.offer_expires_in_seconds}s`
                    : ""}
                  .
                </span>
              </div>
              <Button
                size="sm"
                onClick={() => joinMutation.mutate(queued.code)}
                disabled={joinMutation.isPending}
              >
                Accept seat
              </Button>
            </>
          ) : (
            <div className="flex items-center gap-2">
              <Badge variant="outline">Queued</Badge>
              <span className="text-muted-foreground">
                Position {status?.position ?? "…"} of {status?.length ?? "…"}
                {eta ? ` — ETA ${eta}` : ""}
              </span>
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}
