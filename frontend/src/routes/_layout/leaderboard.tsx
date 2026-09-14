import { type UseQueryResult, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"

import type { LeaderboardEntry } from "@/client"
import { LeaderboardService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { columns } from "@/components/Leaderboard/columns"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { GAMES } from "@/lib/games"

export const Route = createFileRoute("/_layout/leaderboard")({
  component: Leaderboard,
})

const ALL_GAMES = "all"

/** Shared by both tabs (same `LeaderboardEntry` shape) -- distinguishes
 * loading/error from a genuine empty result, so a real backend failure
 * doesn't silently render as "no games yet" forever. */
function LeaderboardTable({
  query,
}: {
  query: UseQueryResult<LeaderboardEntry[]>
}) {
  if (query.isLoading) {
    return (
      <p className="py-6 text-sm text-muted-foreground">
        Loading leaderboard...
      </p>
    )
  }
  if (query.isError) {
    return (
      <p className="py-6 text-sm text-destructive">
        Failed to load leaderboard.
      </p>
    )
  }
  if (!query.data || query.data.length === 0) {
    return (
      <p className="py-6 text-sm text-muted-foreground">
        No finished games yet.
      </p>
    )
  }
  return <DataTable columns={columns} data={query.data} />
}

function Leaderboard() {
  const [game, setGame] = useState<string>(ALL_GAMES)
  const gameFilter = game === ALL_GAMES ? undefined : game

  // Both fetch unconditionally rather than gating the inactive tab's query
  // on `enabled` -- deliberate: the aggregate query is cheap (PROGRESS.md),
  // and prefetching both means switching tabs is instant, never a spinner.
  const accounts = useQuery({
    queryKey: ["leaderboard", "accounts", gameFilter],
    queryFn: async () =>
      (
        await LeaderboardService.accountsLeaderboard({
          query: { game: gameFilter },
        })
      ).data,
  })
  const models = useQuery({
    queryKey: ["leaderboard", "models", gameFilter],
    queryFn: async () =>
      (
        await LeaderboardService.modelsLeaderboard({
          query: { game: gameFilter },
        })
      ).data,
  })

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="font-display text-2xl">Leaderboard</h1>
        <Select value={game} onValueChange={setGame}>
          <SelectTrigger className="w-48" aria-label="Filter by game">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_GAMES}>All games</SelectItem>
            {GAMES.map((g) => (
              <SelectItem key={g.slug} value={g.slug}>
                {g.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Tabs defaultValue="accounts">
        <TabsList>
          <TabsTrigger value="accounts">Accounts</TabsTrigger>
          <TabsTrigger value="models">Models</TabsTrigger>
        </TabsList>
        <TabsContent value="accounts">
          <LeaderboardTable query={accounts} />
        </TabsContent>
        <TabsContent value="models">
          <LeaderboardTable query={models} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
