import { useState, useCallback, useEffect } from 'react'
import { syncEngineering, fetchEngineeringItems } from '../api'

export function useEngineering({ authed }) {
  const [engineeringItems, setEngineeringItems] = useState([])
  const [lastEngineeringSynced, setLastEngineeringSynced] = useState(null)
  const [engineeringSyncing, setEngineeringSyncing] = useState(false)

  const refreshEngineeringItems = useCallback(() => {
    setEngineeringSyncing(true)
    syncEngineering()
      .catch(() => {})  // silently ignore if not configured
      .finally(() => {
        fetchEngineeringItems()
          .then((items) => { setEngineeringItems(items); setLastEngineeringSynced(new Date()) })
          .catch(() => {})
          .finally(() => setEngineeringSyncing(false))
      })
  }, [])

  // Sync on login, then poll every 15 minutes
  useEffect(() => {
    if (!authed) return
    refreshEngineeringItems()
  }, [authed]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const id = setInterval(refreshEngineeringItems, 15 * 60 * 1000)
    return () => clearInterval(id)
  }, [refreshEngineeringItems])

  return { engineeringItems, lastEngineeringSynced, engineeringSyncing, refreshEngineeringItems }
}
