import { useCallback, useEffect, useState } from 'react'
import { fetchBridgeJobStatuses } from '../api'

// 30s: fresher than useEngineering's 15-minute sync (a running/stalled job is worth noticing
// sooner) but well short of the Code tab's own 5s poll (that one only runs for a single job
// while its modal is open; this one runs for every card with a job, for as long as the app
// is open, so a much shorter interval would add needless load for a badge that's only ever
// an at-a-glance indicator, not a live status line).
const POLL_MS = 30_000

// Card-tile status badge data (Board/Today), keyed by card id: { [cardId]: { job_id, status } }.
// Cards with no bridge job are simply absent from the map.
export function useBridgeJobStatuses({ authed }) {
  const [bridgeJobStatuses, setBridgeJobStatuses] = useState({})

  const refresh = useCallback(() => {
    fetchBridgeJobStatuses()
      .then(setBridgeJobStatuses)
      .catch(() => {})  // stale/missing badge data isn't worth surfacing an error for
  }, [])

  useEffect(() => {
    if (!authed) return
    refresh()
  }, [authed]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!authed) return
    const id = setInterval(refresh, POLL_MS)
    return () => clearInterval(id)
  }, [authed, refresh])

  return { bridgeJobStatuses }
}
