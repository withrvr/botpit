/** Not exposed by any API -- these four slugs are this project's whole
 * game catalog (backend/app/games/__init__.py's GAME_CATALOG), stable
 * enough to hardcode rather than add a catalog endpoint nothing else
 * needs yet. Seat bounds come straight from each game's own
 * min_seats/max_seats. Shared by `CreateRoomDialog` (room creation) and
 * the leaderboard's game filter. */
export const GAMES = [
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
