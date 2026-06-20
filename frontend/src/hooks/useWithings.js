import { useState, useEffect, useCallback } from 'react'
import { fetchWithingsStatus, fetchWithingsHealthData, syncWithings, disconnectWithings } from '../api'

export function useWithings({ authed }) {
  const [status, setStatus] = useState(null)      // { connected, last_synced }
  const [healthData, setHealthData] = useState(null) // { measurements, habit_completions }
  const [syncing, setSyncing] = useState(false)

  const loadStatus = useCallback(() => {
    fetchWithingsStatus().then(setStatus).catch(() => {})
  }, [])

  const loadHealthData = useCallback(() => {
    fetchWithingsHealthData(90).then(setHealthData).catch(() => {})
  }, [])

  useEffect(() => {
    if (!authed) return
    loadStatus()
    loadHealthData()
  }, [authed, loadStatus, loadHealthData])

  // Receive notification from the OAuth callback tab
  useEffect(() => {
    const handler = (event) => {
      if (event.origin !== window.location.origin) return
      if (event.data?.type === 'withings-connected') {
        loadStatus()
        loadHealthData()
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [loadStatus, loadHealthData])

  const handleSync = async () => {
    setSyncing(true)
    try {
      await syncWithings()
      loadStatus()
      loadHealthData()
    } catch {
      // ignore
    } finally {
      setSyncing(false)
    }
  }

  const handleDisconnect = async () => {
    await disconnectWithings()
    setStatus({ connected: false, last_synced: null })
    setHealthData(null)
  }

  return { status, healthData, syncing, handleSync, handleDisconnect, loadStatus, loadHealthData }
}
