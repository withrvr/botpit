import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"

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

function Leaderboard() {
  const [game, setGame] = useState<string>(ALL_GAMES)
  const gameFilter = game === ALL_GAMES ? undefined : game

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
          <SelectTrigger className="w-48">
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
          {accounts.data && accounts.data.length > 0 ? (
            <DataTable columns={columns} data={accounts.data} />
          ) : (
            <p className="py-6 text-sm text-muted-foreground">
              No finished games yet.
            </p>
          )}
        </TabsContent>
        <TabsContent value="models">
          {models.data && models.data.length > 0 ? (
            <DataTable columns={columns} data={models.data} />
          ) : (
            <p className="py-6 text-sm text-muted-foreground">
              No finished games yet.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
