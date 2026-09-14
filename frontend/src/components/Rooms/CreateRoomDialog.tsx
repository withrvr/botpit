import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useMemo, useState } from "react"

import { type RoomCreate, type RoomSeatSpec, RoomsService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

/** Not exposed by any API -- these four slugs are this project's whole
 * game catalog (backend/app/games/__init__.py's GAME_CATALOG), stable
 * enough to hardcode rather than add a catalog endpoint nothing else
 * needs yet. Seat bounds come straight from each game's own
 * min_seats/max_seats. */
const GAMES = [
  { slug: "uno_classic", title: "Classic UNO", minSeats: 2, maxSeats: 10 },
  { slug: "uno_flip", title: "UNO Flip!", minSeats: 2, maxSeats: 10 },
  {
    slug: "uno_no_mercy",
    title: "UNO Show 'em No Mercy",
    minSeats: 2,
    maxSeats: 10,
  },
  { slug: "hearts", title: "Hearts", minSeats: 3, maxSeats: 6 },
] as const

/** The persona registry (backend/app/agents/persona.py) is application
 * code, not environment config -- stable enough to hardcode, unlike
 * available LLM models (LLM_MODELS is per-deployment, so an AI seat's
 * `model` is left unset here and defaults server-side instead). */
const PERSONAS = [
  "steady",
  "gambler",
  "quiet-shark",
  "chatterbox",
  "grump",
  "coach",
]

type SeatKind = "human" | "agent"

export function CreateRoomDialog() {
  const [isOpen, setIsOpen] = useState(false)
  const [gameSlug, setGameSlug] = useState<(typeof GAMES)[number]["slug"]>(
    GAMES[0].slug,
  )
  const [visibility, setVisibility] = useState<
    "private" | "unlisted" | "public"
  >("private")
  const [seatKinds, setSeatKinds] = useState<SeatKind[]>(["human", "human"])
  const [seatPersonas, setSeatPersonas] = useState<Record<number, string>>({})
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user: currentUser } = useAuth()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const game = useMemo(
    () => GAMES.find((g) => g.slug === gameSlug) ?? GAMES[0],
    [gameSlug],
  )

  const setSeatCount = (count: number) => {
    setSeatKinds((prev) => {
      const next = [...prev]
      while (next.length < count) next.push("human")
      return next.slice(0, count)
    })
  }

  const mutation = useMutation({
    mutationFn: (data: RoomCreate) => RoomsService.createRoom({ body: data }),
    onSuccess: (res) => {
      showSuccessToast("Room created")
      setIsOpen(false)
      if (res.data)
        navigate({ to: "/rooms/$roomId", params: { roomId: res.data.id } })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["rooms"] })
    },
  })

  const onSubmit = () => {
    // The creator claims the first human seat (RoomService.create's own
    // rule: a human seat's user_id may only ever be the creator's own id,
    // "seating anyone else here would be mass assignment"). Without this,
    // the creator has no seat of their own and the room's first join-by-code
    // claims that seat instead.
    const firstHumanSeat = seatKinds.indexOf("human")
    const seats: RoomSeatSpec[] = seatKinds.map((kind, index) => {
      if (kind === "agent")
        return { kind: "agent", persona: seatPersonas[index] }
      return index === firstHumanSeat && currentUser
        ? { kind: "human", user_id: currentUser.id }
        : { kind: "human" }
    })
    mutation.mutate({
      game: gameSlug,
      seat_count: seatKinds.length,
      visibility,
      seats,
    })
  }

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        setIsOpen(open)
        if (open) {
          setGameSlug(GAMES[0].slug)
          setSeatKinds(["human", "human"])
          setSeatPersonas({})
        }
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2" />
          Create room
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Create a room</DialogTitle>
          <DialogDescription>
            Pick a game, seat count, and who sits where.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-2">
            <Label htmlFor="game">Game</Label>
            <Select
              value={gameSlug}
              onValueChange={(value) => {
                setGameSlug(value as (typeof GAMES)[number]["slug"])
                const nextGame = GAMES.find((g) => g.slug === value) ?? GAMES[0]
                setSeatCount(Math.max(seatKinds.length, nextGame.minSeats))
              }}
            >
              <SelectTrigger id="game">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {GAMES.map((g) => (
                  <SelectItem key={g.slug} value={g.slug}>
                    {g.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="visibility">Visibility</Label>
            <Select
              value={visibility}
              onValueChange={(value) =>
                setVisibility(value as typeof visibility)
              }
            >
              <SelectTrigger id="visibility">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="private">
                  Private (invited seats only)
                </SelectItem>
                <SelectItem value="unlisted">
                  Unlisted (viewable with the link)
                </SelectItem>
                <SelectItem value="public">
                  Public (listed in the lobby)
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label>
                Seats ({seatKinds.length}, {game.minSeats}-{game.maxSeats} for{" "}
                {game.title})
              </Label>
              <div className="flex gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={seatKinds.length <= game.minSeats}
                  onClick={() => setSeatCount(seatKinds.length - 1)}
                >
                  -
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={seatKinds.length >= game.maxSeats}
                  onClick={() => setSeatCount(seatKinds.length + 1)}
                >
                  +
                </Button>
              </div>
            </div>
            <div className="flex flex-col gap-2">
              {seatKinds.map((kind, index) => (
                <div key={index} className="flex items-center gap-2">
                  <span className="w-14 text-sm text-muted-foreground">
                    Seat {index}
                  </span>
                  <Select
                    value={kind}
                    onValueChange={(value) =>
                      setSeatKinds((prev) =>
                        prev.map((k, i) =>
                          i === index ? (value as SeatKind) : k,
                        ),
                      )
                    }
                  >
                    <SelectTrigger
                      className="w-32"
                      aria-label={`Seat ${index} kind`}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="human">Human (open)</SelectItem>
                      <SelectItem value="agent">AI</SelectItem>
                    </SelectContent>
                  </Select>
                  {kind === "agent" ? (
                    <Select
                      value={seatPersonas[index] ?? "steady"}
                      onValueChange={(value) =>
                        setSeatPersonas((prev) => ({ ...prev, [index]: value }))
                      }
                    >
                      <SelectTrigger
                        className="w-36"
                        aria-label={`Seat ${index} persona`}
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {PERSONAS.map((p) => (
                          <SelectItem key={p} value={p}>
                            {p}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter>
          <LoadingButton onClick={onSubmit} loading={mutation.isPending}>
            Create
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
