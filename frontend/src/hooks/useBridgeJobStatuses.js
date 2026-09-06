import { useCallback, useEffect, useState } from 'react'
import { fetchBridgeJobStatuses } from '../api'

// 60s: fresher than useEngineering's 15-minute sync (a running/stalled job is worth noticing
// sooner) but well short of the Code tab's own 5s poll (that one only runs for a single job
// while its modal is open; this one runs for every card with a job, for as long as the app
// is open, so a much shorter interval would add needless load for a badge that's only ever
// an at-a-glance indicator, not a live status line).
const POLL_MS = 60_000

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

  // Only polls while the tab is actually visible -- this hook lives at the App shell level
  // and runs for as long as the app is open, with no page to scope it to, so a tab left open
  // in the background (or overnight) previously polled forever regardless. On a Cloud Run
  // deployment with --no-cpu-throttling (CPU billed for the whole time an instance is warm,
  // not just during request handling -- see dev.sh/deploy.yml), a forgotten background tab
  // polling every 30s kept the instance perpetually billed for no real benefit: nobody's
  // looking at the badge while the tab is hidden. Refreshes immediately on regaining
  // visibility so the badge doesn't show stale data for up to a full interval on return.
  useEffect(() => {
    if (!authed) return
    let id = null
    const start = () => { if (!id) id = setInterval(refresh, POLL_MS) }
    const stop = () => { if (id) { clearInterval(id); id = null } }
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refresh()
        start()
      } else {
        stop()
      }
    }
    if (document.visibilityState === 'visible') start()
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [authed, refresh])

  return { bridgeJobStatuses }
}
