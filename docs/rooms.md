# Rooms

Status: skeleton, filled in during Phase 7.

A room is one instance of a game: game type, seat list (each seat human
open / human invited / AI model+persona), status
(waiting / in_progress / finished / stopped), join code, visibility
(private / unlisted / public), creator (implicit room moderator).

Hard caps enforced server-side: seat count, viewers per room, concurrent
games, total connections. Overflow joins a FIFO queue. See
`docs/capacity-and-queueing.md`.
