import { useState, useCallback, useEffect, useRef } from 'react'
import { syncEngineering, fetchEngineeringItems } from '../api'

export function useEngineering({ authed }) {
  const [engineeringItems, setEngineeringItems] = useState([])
  const [lastEngineeringSynced, setLastEngineeringSynced] = useState(null)
  const [engineeringSyncing, setEngineeringSyncing] = useState(false)
  const syncInFlight = useRef(false)

  const refreshEngineeringItems = useCallback(() => {
    // Guards against overlapping triggers -- e.g. the login sync below and the
    // Engineering page's own on-open sync both firing on the same initial
    // render when Engineering is the configured default landing page.
    if (syncInFlight.current) return
    syncInFlight.current = true
    setEngineeringSyncing(true)
    syncEngineering()
      .catch(() => {})  // silently ignore if not configured
      .finally(() => {
        fetchEngineeringItems()
          .then((items) => { setEngineeringItems(items); setLastEngineeringSynced(new Date()) })
          .catch(() => {})
          .finally(() => {
            syncInFlight.current = false
            setEngineeringSyncing(false)
          })
      })
  }, [])

  // Sync on login, then poll every 15 minutes
  useEffect(() => {
    if (!authed) return
    refreshEngineeringItems()
  }, [authed]) // eslint-disable-line react-hooks/exhaustive-deps

  // Only polls while the tab is actually visible -- this hook lives at the App shell level
  // and runs for as long as the app is open, with no page to scope it to, so a tab left open
  // in the background (or overnight) previously polled every 15 minutes forever regardless.
  // Same cost-incident pattern as useBridgeJobStatuses (see its own comment for the Cloud
  // Run --no-cpu-throttling mechanics) -- this hook just never got the same fix generalized
  // to it. Refreshes immediately on regaining visibility so items don't show stale data for
  // up to a full interval on return.
  useEffect(() => {
    if (!authed) return
    let id = null
    const start = () => { if (!id) id = setInterval(refreshEngineeringItems, 15 * 60 * 1000) }
    const stop = () => { if (id) { clearInterval(id); id = null } }
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshEngineeringItems()
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
  }, [authed, refreshEngineeringItems])

  return { engineeringItems, lastEngineeringSynced, engineeringSyncing, refreshEngineeringItems }
}
