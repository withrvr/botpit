# Admin and observability

Status: logging design decided in Phase 1; dashboard lands in Phase 10;
metrics and the last missing wide-event kind (`ws.session`) land in
Phase 13.

## Logging: wide events

Reference: https://loggingsucks.com/. One comprehensive JSON event per unit
of work, emitted once at completion, carrying every field that could matter
for debugging. Units of work in botpit:

| Kind | Emitted when | Key fields |
|---|---|---|
| `http.request` | response sent | method, path, status, duration_ms, user_id, request_id |
| `ws.session` | websocket closes | room_id, user_id, subscriber_kind, seat, events_sent, duration_ms, close_reason (set only on a clean client disconnect; any other exception's own type/message land in `error.*` instead, same as every other wide event) |
| `game.turn` | a seat's turn finishes or fails (docs originally called this `agent.turn`; the actual `event_kind` -- and every seat kind, not just agents -- has always been `game.turn`) | game_id, turn_id, seat, seat_kind, model, retries, fallback, move, attempts; an agent seat's own LLM calls (`AgentController._metrics`) additionally add one `llm.calls` entry per attempt: model, latency_ms, prompt_tokens, completion_tokens, ok, error |
| `game` | game finishes or is stopped | room_id, game_id, game_type, seats, turns, duration_ms, winner, ranking |

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

## Metrics (Phase 13) -- done

Prometheus `GET /metrics` (unauthenticated, matching `GET /health`; a
dedicated `CollectorRegistry` in `app/metrics.py`, not
`prometheus_client`'s process-global default, so this endpoint never
leaks unrelated process metrics):

| Metric | Kind | Labels |
|---|---|---|
| `botpit_active_games` | gauge | -- |
| `botpit_active_ws_connections` | gauge | -- |
| `botpit_agent_turn_latency_seconds` | histogram | `model` |
| `botpit_agent_turn_errors_total` | counter | `model`, `error` |
| `botpit_agent_turn_timeouts_total` | counter | `model` (only real `kind="agent"` seats, not `"scripted"`) |
| `botpit_moves_total` | counter | -- ("moves per minute": graph this counter's `rate()`, Prometheus's own idiom, rather than botpit computing and storing a per-minute number itself) |
| `botpit_model_swaps_total` | counter | `base_url` |

`botpit_model_swaps_total` counts a real signal (DECISIONS.md §13: two
consecutive LLM calls to the same Ollama endpoint that used different
models -- a likely VRAM swap), via `app.metrics.note_llm_call`. It is
**not** the model-swap-*aware scheduler* §13 also describes (grouping
concurrent AI turns by model to minimise these swaps) -- that scheduler
does not exist yet; this phase only adds the counter that would prove
whether it's worth building.

## Admin dashboard (Phase 10/13)

All rooms, per-room seats and status, force-stop, live log tail, runtime
settings (capacity caps), feature flags.

## Leaderboard (Phase 12, docs/leaderboard.md)

Per human account and per model, DECISIONS.md §11. Ended up as its own
top-level `/leaderboard` page rather than folded into the admin dashboard
above: `leaderboard:view` is granted to every default role
(`admin`/`player`/`spectator`), since a leaderboard is meant to be seen by
players, not gated behind admin-only access.
