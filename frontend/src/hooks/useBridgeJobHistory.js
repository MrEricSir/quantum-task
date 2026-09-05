import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchBridgeJobHistory } from '../api'

// Between the Code tab's 5s single-job poll (too aggressive for a whole list) and the
// board-wide 30s badge poll (too slow to feel current while someone's actually looking at
// this card's own history right now).
const POLL_MS = 10_000

// Jobs in these statuses can still change -- keep polling while any are present. A card
// whose jobs are all terminal (done/error/stalled/etc.) has nothing left to refresh.
const ACTIVE_STATUSES = ['pending', 'running', 'blocked', 'needs_confirmation']

// Every bridge job ever run against a card, for the card detail panel's Bridge history
// section -- distinct from useAssistCode's own job-chain state, which is scoped to the
// Assist modal's Code tab and only tracks the current/latest job.
export function useBridgeJobHistory(cardId) {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  // Mirrors `jobs`, but readable synchronously from the interval callback below without
  // that callback needing to be re-created (and the interval re-armed) on every fetch --
  // avoids a race between "the interval only exists once a fetch has resolved with an
  // active job" and "the timer needs a full POLL_MS from when it was armed to fire."
  const jobsRef = useRef([])

  const refresh = useCallback(() => {
    if (!cardId) return
    fetchBridgeJobHistory(cardId)
      .then((data) => { jobsRef.current = data; setJobs(data) })
      .catch(() => {})  // stale/missing history isn't worth surfacing an error for
      .finally(() => setLoading(false))
  }, [cardId])

  useEffect(() => {
    jobsRef.current = []
    setJobs([])
    setLoading(true)
    refresh()
  }, [refresh])

  useEffect(() => {
    const id = setInterval(() => {
      if (jobsRef.current.some((j) => ACTIVE_STATUSES.includes(j.status))) refresh()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [refresh])

  return { jobs, loading, refresh }
}
