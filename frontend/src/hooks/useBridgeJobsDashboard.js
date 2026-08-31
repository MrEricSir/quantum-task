import { useCallback, useEffect, useState } from 'react'
import { fetchBridgeJobsDashboard } from '../api'

// Refreshes often enough that "just queued"/"just finished" feels current on a page
// that's actually open and being looked at, without hammering the backend the way the
// Code tab's 5s single-job poll would if applied across every job at once.
const POLL_MS = 15_000

// Fleet-level view of bridge jobs for the Engineering page dashboard. Deliberately
// page-scoped (called directly from EngineeringPage, not lifted to App.jsx like
// useBridgeJobStatuses) -- nothing else needs this data, so the poll should start and
// stop with the page being open, not run for the lifetime of the whole app.
export function useBridgeJobsDashboard() {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(() => {
    fetchBridgeJobsDashboard()
      .then(setJobs)
      .catch(() => {})  // stale/missing dashboard data isn't worth surfacing an error for
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, POLL_MS)
    return () => clearInterval(id)
  }, [refresh])

  return { jobs, loading }
}
