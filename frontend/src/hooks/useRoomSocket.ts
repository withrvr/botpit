import { useEffect, useReducer, useRef, useState } from "react"

import { RoomsService } from "@/client"
import type { EventEnvelope } from "@/lib/events"
import {
  applyRoomEvent,
  INITIAL_ROOM_LIVE_STATE,
  RESET_EVENT_TYPE,
  type RoomLiveState,
} from "@/lib/roomLiveState"

export type SocketStatus = "connecting" | "open" | "closed"

interface UseRoomSocketResult {
  live: RoomLiveState
  status: SocketStatus
}

/** Review fix: a browser's WebSocket API can't set an Authorization
 * header, so the token has to travel in the URL -- but the real,
 * long-lived access_token in a URL is a much wider log-exposure surface
 * (proxy/server access logs, browser history) than a header ever gets.
 * A ~30s ticket minted just before connecting keeps that window tiny. */

function socketUrl(roomId: string, ticket: string, since: string): string {
  const apiBase = import.meta.env.VITE_API_URL
  const base = apiBase ? new URL(apiBase) : window.location
  const wsProtocol = base.protocol === "https:" ? "wss:" : "ws:"
  const params = new URLSearchParams({ token: ticket, since })
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
 * connect for a given room always replays from the start (`since=0`) so
 * a page opened mid-game reconstructs the room's current state, not just
 * what happens to arrive after the page loads.
 *
 * Review fix: state and the resume cursor both reset whenever `roomId`
 * itself changes (not just on unmount) -- without this, navigating
 * straight from one room to another (no full remount in between) kept
 * the previous room's finished/hand/reasoning state visible until enough
 * new events arrived to overwrite each slice, and resumed the new room's
 * feed from the old room's cursor instead of replaying from the start.
 */
export function useRoomSocket(roomId: string | undefined): UseRoomSocketResult {
  const [live, dispatch] = useReducer(applyRoomEvent, INITIAL_ROOM_LIVE_STATE)
  const [status, setStatus] = useState<SocketStatus>("connecting")
  const lastStreamId = useRef<string>("0")

  useEffect(() => {
    if (!roomId) return undefined
    lastStreamId.current = "0"
    dispatch({ type: RESET_EVENT_TYPE } as EventEnvelope)

    let cancelled = false
    let socket: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let retryDelay = INITIAL_RETRY_MS

    async function connect() {
      setStatus("connecting")
      let ticket: string
      try {
        const res = await RoomsService.wsTicket({
          path: { room_id: roomId as string },
        })
        if (!res.data) throw new Error("no ticket in response")
        ticket = res.data.ticket
      } catch {
        if (cancelled) return
        setStatus("closed")
        retryTimer = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, MAX_RETRY_MS)
          connect()
        }, retryDelay)
        return
      }
      if (cancelled) return

      socket = new WebSocket(
        socketUrl(roomId as string, ticket, lastStreamId.current),
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
