import { useEffect, useReducer, useRef, useState } from "react"

import type { EventEnvelope } from "@/lib/events"
import {
  applyRoomEvent,
  INITIAL_ROOM_LIVE_STATE,
  type RoomLiveState,
} from "@/lib/roomLiveState"

export type SocketStatus = "connecting" | "open" | "closed"

interface UseRoomSocketResult {
  live: RoomLiveState
  status: SocketStatus
}

function socketUrl(roomId: string, token: string, since: string): string {
  const apiBase = import.meta.env.VITE_API_URL
  const base = apiBase ? new URL(apiBase) : window.location
  const wsProtocol = base.protocol === "https:" ? "wss:" : "ws:"
  const params = new URLSearchParams({ token, since })
  return `${wsProtocol}//${base.host}/api/v1/rooms/${roomId}/ws?${params.toString()}`
}

const INITIAL_RETRY_MS = 500
const MAX_RETRY_MS = 10_000

/**
 * Connects to the room's push transport (WS /rooms/{room_id}/ws,
 * docs/rooms.md) and folds every event into `RoomLiveState` via
 * `applyRoomEvent`. Reconnects with backoff, resuming from the last seen
 * `stream_id` so a dropped connection never re-shows or drops an event
 * (the same `since` contract the polling route uses). The very first
 * connect always replays from the start (`since=0-0`) so a page opened
 * mid-game reconstructs the room's current state, not just what happens
 * to arrive after the page loads.
 */
export function useRoomSocket(roomId: string | undefined): UseRoomSocketResult {
  const [live, dispatch] = useReducer(applyRoomEvent, INITIAL_ROOM_LIVE_STATE)
  const [status, setStatus] = useState<SocketStatus>("connecting")
  const lastStreamId = useRef<string>("0-0")

  useEffect(() => {
    if (!roomId) return undefined
    let cancelled = false
    let socket: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let retryDelay = INITIAL_RETRY_MS

    function connect() {
      const token = localStorage.getItem("access_token")
      if (!token) {
        setStatus("closed")
        return
      }
      setStatus("connecting")
      socket = new WebSocket(
        socketUrl(roomId as string, token, lastStreamId.current),
      )

      socket.onopen = () => {
        if (cancelled) return
        setStatus("open")
        retryDelay = INITIAL_RETRY_MS
      }
      socket.onmessage = (message) => {
        if (cancelled) return
        const event = JSON.parse(message.data as string) as EventEnvelope
        if (event.stream_id) lastStreamId.current = event.stream_id
        dispatch(event)
      }
      socket.onclose = () => {
        if (cancelled) return
        setStatus("closed")
        retryTimer = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, MAX_RETRY_MS)
          connect()
        }, retryDelay)
      }
      socket.onerror = () => {
        socket?.close()
      }
    }

    connect()

    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
      socket?.close()
    }
  }, [roomId])

  return { live, status }
}
