/**
 * Folds the room event stream (docs/events.md) into compact derived state,
 * rather than handing every consumer a growing raw event array to re-scan
 * on every render -- the event stream fires often (a turn produces several
 * events in quick succession), so this is where the "avoid unnecessary
 * re-renders" concern (react-performance) actually gets addressed: each
 * consumer reads only the slice of `RoomLiveState` it needs.
 *
 * Pure and exported standalone so it's testable without a real socket.
 */

import type {
  AgentReasoningPayload,
  ChatMessagePayload,
  EventEnvelope,
  GameFinishedPayload,
  MoveAppliedPayload,
  MoveRejectedPayload,
  PlayerViewPayload,
  RoomStoppedPayload,
} from "@/lib/events"

export interface ReasoningEntry {
  seat: number
  model: string
  text: string
  ts: string
}

export interface MoveLogEntry {
  seat: number
  description: string
  ts: string
}

export interface ChatEntry {
  seat: number | null
  text: string
  ts: string
}

export interface RoomLiveState {
  roomStatus: "waiting" | "in_progress" | "finished" | "stopped" | null
  /** Bumped on any event that changes the room's seat list (fill, or a
   * force-start backfill) -- REST-fetched room data (kind/status/persona
   * per seat) has no live equivalent of its own, so a consumer diffs this
   * against what it last saw to know when to refetch `GET /rooms/{id}`
   * rather than rendering a stale snapshot from before someone joined. */
  seatsVersion: number
  currentSeat: number | null
  /** Keyed by seat. A non-privileged player only ever receives their own
   * seat's view_updated events (server-side filter_for_subscriber), so
   * this naturally holds just one entry for them and every seat for a
   * privileged subscriber -- no client-side visibility logic needed. */
  viewsBySeat: Record<number, PlayerViewPayload>
  finished: GameFinishedPayload | null
  stopped: RoomStoppedPayload | null
  /** Latest reasoning per seat (privileged-only events; empty for a
   * regular player, who never receives them). */
  reasoningBySeat: Record<number, ReasoningEntry>
  lastRejection: MoveRejectedPayload | null
  recentMoves: MoveLogEntry[]
  chatLog: ChatEntry[]
}

export const INITIAL_ROOM_LIVE_STATE: RoomLiveState = {
  roomStatus: null,
  seatsVersion: 0,
  currentSeat: null,
  viewsBySeat: {},
  finished: null,
  stopped: null,
  reasoningBySeat: {},
  lastRejection: null,
  recentMoves: [],
  chatLog: [],
}

const MAX_LOG_ENTRIES = 100

export function applyRoomEvent(
  state: RoomLiveState,
  event: EventEnvelope,
): RoomLiveState {
  switch (event.type) {
    case "room.created":
      return { ...state, roomStatus: "waiting" }
    case "room.seat_filled":
      return { ...state, seatsVersion: state.seatsVersion + 1 }
    case "game.started":
      return {
        ...state,
        roomStatus: "in_progress",
        seatsVersion: state.seatsVersion + 1,
        finished: null,
        stopped: null,
      }
    case "game.turn_started": {
      const p = event.payload as { seat: number }
      return { ...state, currentSeat: p.seat }
    }
    case "game.view_updated": {
      const p = event.payload as { seat: number; view: PlayerViewPayload }
      return {
        ...state,
        viewsBySeat: { ...state.viewsBySeat, [p.seat]: p.view },
      }
    }
    case "game.move_applied": {
      const p = event.payload as unknown as MoveAppliedPayload
      const entry: MoveLogEntry = {
        seat: p.seat,
        description: p.description,
        ts: event.ts,
      }
      return {
        ...state,
        recentMoves: [...state.recentMoves, entry].slice(-MAX_LOG_ENTRIES),
      }
    }
    case "game.move_rejected":
      return {
        ...state,
        lastRejection: event.payload as unknown as MoveRejectedPayload,
      }
    case "game.finished":
      return {
        ...state,
        roomStatus: "finished",
        finished: event.payload as unknown as GameFinishedPayload,
      }
    case "room.stopped":
      return {
        ...state,
        roomStatus: "stopped",
        stopped: event.payload as unknown as RoomStoppedPayload,
      }
    case "agent.reasoning": {
      const p = event.payload as unknown as AgentReasoningPayload
      return {
        ...state,
        reasoningBySeat: {
          ...state.reasoningBySeat,
          [p.seat]: {
            seat: p.seat,
            model: p.model,
            text: p.text,
            ts: event.ts,
          },
        },
      }
    }
    case "chat.message": {
      const p = event.payload as unknown as ChatMessagePayload
      const entry: ChatEntry = { seat: p.seat, text: p.text, ts: event.ts }
      return {
        ...state,
        chatLog: [...state.chatLog, entry].slice(-MAX_LOG_ENTRIES),
      }
    }
    default:
      return state
  }
}
