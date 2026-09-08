import { useState, useEffect, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { fetchWithingsStatus, fetchWithingsHealthData, syncWithings, disconnectWithings, fetchWithingsGoals, saveWithingsGoals, createHealthMeasurement, deleteHealthMeasurement } from '../api'
import { HABITS_QUERY_KEY } from './useHabits'

export function useWithings({ authed }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState(null)      // { connected, last_synced }
  const [healthData, setHealthData] = useState(null) // { measurements, habit_completions }
  const [healthGoals, setHealthGoals] = useState(null) // { steps, fat_ratio, weight }
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState(null)

  const loadStatus = useCallback(() => {
    fetchWithingsStatus().then((s) => {
      setStatus(s)
      // Clear a stale sync error if the connection is confirmed good
      if (s?.connected) setSyncError(null)
    }).catch(() => {})
  }, [])

  const loadHealthData = useCallback(() => {
    fetchWithingsHealthData(90).then(setHealthData).catch(() => {})
  }, [])

  const loadHealthGoals = useCallback(() => {
    fetchWithingsGoals().then(setHealthGoals).catch(() => {})
  }, [])

  useEffect(() => {
    if (!authed) return
    loadStatus()
    loadHealthData()
    loadHealthGoals()
  }, [authed, loadStatus, loadHealthData, loadHealthGoals])

  // Receive notification from the OAuth callback tab
  useEffect(() => {
    const handler = (event) => {
      if (event.origin !== window.location.origin) return
      if (event.data?.type === 'withings-connected') {
        // Trigger a sync immediately so data appears without a manual step
        syncWithings()
          .then(() => { loadStatus(); loadHealthData() })
          .catch(() => { loadStatus(); loadHealthData() })
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [loadStatus, loadHealthData])

  const handleSaveGoals = async (goals) => {
    const updated = await saveWithingsGoals(goals)
    setHealthGoals(updated)
    return updated
  }

  const handleSync = async () => {
    setSyncing(true)
    setSyncError(null)
    try {
      const result = await syncWithings()
      if (result?.ok === false) {
        setSyncError(result.error ?? 'sync_failed')
      } else {
        loadStatus()
        loadHealthData()
      }
    } catch {
      setSyncError('sync_failed')
    } finally {
      setSyncing(false)
    }
  }

  const handleDisconnect = async () => {
    await disconnectWithings()
    setStatus({ connected: false, last_synced: null })
    setHealthData(null)
  }

  const handleAddMeasurement = async (data) => {
    const result = await createHealthMeasurement(data)
    loadHealthData()
    // A manually-entered value may satisfy a linked experiment habit's goal and get
    // auto-checked server-side (see routers/health.py's auto_check_habits_for_date) --
    // refetch so the Habits section reflects it immediately, same as WorkoutLog's onMutate.
    queryClient.invalidateQueries({ queryKey: HABITS_QUERY_KEY })
    return result
  }

  const handleDeleteMeasurement = async (id) => {
    await deleteHealthMeasurement(id)
    loadHealthData()
  }

  return {
    status, healthData, healthGoals, syncing, syncError,
    handleSync, handleDisconnect, handleSaveGoals, loadStatus, loadHealthData,
    handleAddMeasurement, handleDeleteMeasurement,
  }
}
