# Room events

Every room produces one ordered stream of events. All readers (spectator UI,
admin UI, each player's WebSocket, the agent runner) consume that one stream;
the server filters it per subscriber before anything leaves the process.

Code: `backend/app/events/` (`schema.py`, `filter.py`, `bus.py`, `redis_bus.py`).

## Envelope

| field         | type              | notes                                                        |
| ------------- | ----------------- | ------------------------------------------------------------ |
| `event_id`    | `str`             | uuid4 hex, set at construction                               |
| `room_id`     | `str`             |                                                              |
| `seq`         | `int \| None`     | per-room monotonic 1,2,3…; `None` until the bus publishes it |
| `type`        | `str`             | dotted, e.g. `game.move_applied`; from `EVENT_TYPES`         |
| `ts`          | `datetime` (UTC)  |                                                              |
| `visibility`  | `Visibility`      | see below                                                    |
| `payload`     | `dict[str, Any]`  | `model_dump()` of the typed payload                          |
| `correlation` | `dict[str, str]`  | `request_id`, `turn_id`, … default empty                     |
| `stream_id`   | `str \| None`     | bus position; pass back as `since` to resume                 |

Build one with `make_event(room_id, payload, visibility, correlation=None)`;
`type` is looked up from the payload class in `EVENT_TYPES`.

Payload models (Phase 3+ fills them in): `RoomCreated`, `SeatFilled`,
`GameStarted`, `TurnStarted`, `MoveApplied`, `HandUpdated`, `AgentReasoning`,
`AgentTurnMetrics`, `ChatMessage`, `GameFinished`, `RoomStopped`.

## Visibility

`PUBLIC`, `seat(n)` and `PRIVILEGED` are the three values (discriminated on `kind`).

| visibility   | privileged subscriber | player at seat n | player at seat m≠n |
| ------------ | --------------------- | ---------------- | ------------------ |
| `public`     | yes                   | yes              | yes                |
| `seat(n)`    | yes                   | yes              | no                 |
| `privileged` | yes                   | no               | no                 |
| anything else| yes                   | no               | no                 |

`filter_for_subscriber(event, Subscriber(kind=..., seat=...))` returns the
event or `None`. It is allow-list only: a non-privileged subscriber gets an
event only when the visibility is recognised *and* matches. `HandUpdated`
must be published with `seat(n)`, `AgentReasoning` with `PRIVILEGED`.

## Bus

```python
class EventBus(Protocol):
    async def publish(event) -> EventEnvelope          # assigns seq + stream_id
    def subscribe(room_id, since=None) -> AsyncIterator[EventEnvelope]
    async def history(room_id, since=None, limit=1000) -> list[EventEnvelope]
    async def close()
```

`since`: `None` = live only from now; `"0"` = replay everything retained,
then live; `<stream_id>` = everything after that id, then live (reconnect
catch-up). Every subscriber gets every event (fan-out); there are no consumer
groups. `create_event_bus(settings)` picks the implementation from
`settings.event_bus`.

### In-memory (`event_bus=memory`)

Single process only. Per-room `deque(maxlen=event_stream_maxlen)`, oldest
dropped; `stream_id` is `"<seq>-0"`.

### Redis Streams (`event_bus=redis`)

Multiple uvicorn workers share one Redis. Streams rather than Pub/Sub because
Pub/Sub is fire-and-forget: a client that reconnects has lost whatever was
sent while it was away. A stream keeps the last `maxlen` entries, so a client
resumes with `XREAD` from its last `stream_id`.

| key                              | use                                          |
| -------------------------------- | -------------------------------------------- |
| `botpit:room:{room_id}:events`   | stream, `XADD MAXLEN ~ maxlen`, one field `e` (envelope JSON) |
| `botpit:room:{room_id}:seq`      | `WATCH`+`MULTI` with `XADD`; gives `seq`     |

`subscribe` is an `XREAD BLOCK 1000` loop that re-checks `close()` between
blocks; `history` is `XRANGE`. `~` trimming is approximate: Redis keeps at
least `maxlen`, possibly a little more.

## Health

Every bus implements `ping() -> bool`, reported by `GET /health` as
`event_bus: "<memory|redis>:<ok|down>"`.

## Multi-worker safety

`RedisStreamsEventBus.publish` reads the per-room counter under `WATCH`,
then `SET`s it and `XADD`s in one `MULTI` block. If another worker bumps
the counter first the transaction aborts and retries, so `seq` order and
stream order never disagree. The in-memory bus is single-process by
definition; `create_event_bus` picks it only when `EVENT_BUS=memory`.

## Referee events (Phase 3)

| Type | Visibility | Payload |
|---|---|---|
| `game.started` | public | game, seats, seed, seat_specs |
| `game.turn_started` | public | seat, turn_id, deadline_ms, kind |
| `game.move_applied` | public | seat, move, description, move_index |
| `game.move_rejected` | seat | seat, move, reason, attempt |
| `game.turn_timed_out` | public | seat, fallback_move |
| `game.view_updated` | seat | seat, view (the seat's full `PlayerView`) |
| `game.finished` | public | winner_seat, ranking, scores |
| `room.stopped` | public | reason |

A privileged subscriber receives every `game.view_updated`, which is what
the click-any-seat focus view renders; a player subscriber receives only
its own.
