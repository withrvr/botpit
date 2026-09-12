# Capacity and queueing

Status: skeleton, researched and built in Phase 7.

Caps: `MAX_CONCURRENT_GAMES` (runtime setting), global connection cap,
per-room seat cap, per-room viewer cap. Overflow enters a fair FIFO queue
with position feedback and automatic seat offer. Backing store decided in
Phase 7 (Redis is already in the stack for the event bus).
