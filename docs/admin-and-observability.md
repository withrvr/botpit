# Admin and observability

Status: logging design decided in Phase 1; dashboard and metrics land in
Phases 10 and 13.

## Logging: wide events

Reference: https://loggingsucks.com/. One comprehensive JSON event per unit
of work, emitted once at completion, carrying every field that could matter
for debugging. Units of work in botpit:

| Kind | Emitted when | Key fields |
|---|---|---|
| `http.request` | response sent | method, path, status, duration_ms, user_id, request_id |
| `ws.session` | websocket closes | room_id, subscriber kind, seat, events_sent, duration_ms, close reason |
| `agent.turn` | agent turn finishes or fails | game_id, turn_id, seat, model, persona, prompt_tokens, completion_tokens, latency_ms per call, retries, parse_ok, move, error |
| `game` | game finishes or is stopped | game_id, game_type, seats (human/AI, model), turns, duration_ms, winner, swaps |

Correlation ids (`request_id`, `game_id`, `room_id`, `turn_id`, `user_id`,
`seat`) are set on the current wide event and inherited by nested events.

Tail sampling: errors and slow events are always kept; the rest are kept
with probability `LOG_SAMPLE_RATE` (1.0 in dev). Sampling applies only to
wide events, never to ordinary log lines.

Formats: `LOG_FORMAT=json` (one object per line, for files and shipping)
or `console` (human-readable, dev default). `LOG_FILE` adds a JSON file
handler; the admin dashboard's live tail reads the same stream.

## Health

- `GET /health`: app liveness plus DB and bus checks.
- `GET /health/llm`: provider, reachability, models present/missing.

## Metrics (Phase 13)

Prometheus `GET /metrics`: active games, active connections, agent turn
latency histogram per model, agent error/timeout counters per model,
moves per minute, model swaps.

## Admin dashboard (Phase 10/13)

All rooms, per-room seats and status, force-stop, live log tail, runtime
settings (capacity caps), feature flags.

## Leaderboard (Phase 12, docs/leaderboard.md)

Per human account and per model, DECISIONS.md §11. Ended up as its own
top-level `/leaderboard` page rather than folded into the admin dashboard
above: `leaderboard:view` is granted to every default role
(`admin`/`player`/`spectator`), since a leaderboard is meant to be seen by
players, not gated behind admin-only access.
