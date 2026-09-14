import type { ColumnDef } from "@tanstack/react-table"

import type { LeaderboardEntry } from "@/client"
import type { features } from "@/components/Common/DataTable"

export const columns: ColumnDef<typeof features, LeaderboardEntry>[] = [
  {
    id: "rank",
    header: "#",
    cell: ({ row }) => (
      <span className="text-muted-foreground">{row.index + 1}</span>
    ),
  },
  {
    accessorKey: "display_name",
    header: "Name",
    cell: ({ row }) => (
      <span className="font-medium">{row.original.display_name}</span>
    ),
  },
  {
    accessorKey: "games_played",
    header: "Games",
  },
  {
    accessorKey: "wins",
    header: "Wins",
  },
  {
    accessorKey: "win_rate",
    header: "Win rate",
    cell: ({ row }) => `${Math.round(row.original.win_rate * 100)}%`,
  },
]
