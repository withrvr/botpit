/**
 * The room/game event envelope (docs/events.md, docs/api/api-reference.md).
 * Hand-written: the backend hands both /events and /ws raw dicts, not a
 * Pydantic response_model, so this shape never appears in the generated
 * OpenAPI client (src/client) -- keep this in sync with
 * backend/app/events/schema.py's EventEnvelope/Visibility by hand.
 */

export type Visibility =
  | { kind: "public" }
  | { kind: "seat"; seat: number }
  | { kind: "privileged" }

export interface EventEnvelope<Payload = Record<string, unknown>> {
  event_id: string
  room_id: string
  seq: number | null
  type: string
  ts: string
  visibility: Visibility
  payload: Payload
  correlation: Record<string, string>
  stream_id: string | null
}

export interface RoomCreatedPayload {
  game: string
  seats: number
}

export interface SeatFilledPayload {
  seat: number
  player: string
}

export interface RoomStoppedPayload {
  reason: string
}

export interface GameStartedPayload {
  game: string
  seats: number
  seed: number
  seat_specs: unknown[]
}

export interface TurnStartedPayload {
  seat: number
  turn_id: string
  deadline_ms: number
  kind: "human" | "agent"
}

export interface MoveAppliedPayload {
  seat: number
  move: Record<string, unknown>
  description: string
  move_index: number
}

export interface MoveRejectedPayload {
  seat: number
  move: Record<string, unknown>
  reason: string
  attempt: number
}

export interface TurnTimedOutPayload {
  seat: number
  fallback_move: Record<string, unknown>
}

export interface PlayerViewPayload {
  slug: string
  seat: number
  phase: string
  current_seat: number | null
  direction: 1 | -1
  hand: Record<string, unknown>[]
  hand_counts: number[]
  table: Record<string, unknown>
  legal_moves: Record<string, unknown>[]
  scores: number[]
  eliminated: number[]
  terminal: boolean
}

export interface ViewUpdatedPayload {
  seat: number
  view: PlayerViewPayload
}

export interface GameFinishedPayload {
  winner_seat: number | null
  ranking: number[]
  scores: number[]
}

export interface AgentReasoningPayload {
  seat: number
  model: string
  text: string
}

export interface ChatMessagePayload {
  seat: number | null
  text: string
}
