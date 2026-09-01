import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchTrip, startTrip, updateTrip, endTrip, deleteTrip } from '../api'
import { HABITS_QUERY_KEY } from './useHabits'

export const TRIP_QUERY_KEY = ['trip']

export function useTrip({ authed }) {
  const queryClient = useQueryClient()

  const {
    data: trip = null,
    isLoading: tripLoading,
  } = useQuery({
    queryKey: TRIP_QUERY_KEY,
    queryFn: fetchTrip,
    enabled: !!authed,
    staleTime: 5 * 60 * 1000,
  })

  // Every trip mutation triggers a full habit-streak recompute server-side (streak.py's
  // trip-day freezing) -- HABITS_QUERY_KEY has to be invalidated alongside TRIP_QUERY_KEY
  // or the streak/heatmap shown on the Health/Today pages goes stale, same class of bug
  // fixed for the experiment-dismiss flow (see HealthPage.jsx's ExperimentCard).
  const invalidateAfterMutation = () => {
    queryClient.invalidateQueries({ queryKey: TRIP_QUERY_KEY })
    queryClient.invalidateQueries({ queryKey: HABITS_QUERY_KEY })
  }

  const handleStartTrip = async (name, startDate) => {
    await startTrip(name, startDate)
    invalidateAfterMutation()
  }

  const handleUpdateTrip = async (tripId, name, startDate) => {
    await updateTrip(tripId, name, startDate)
    invalidateAfterMutation()
  }

  const handleEndTrip = async (tripId) => {
    const result = await endTrip(tripId)
    invalidateAfterMutation()
    return result
  }

  const handleDeleteTrip = async (tripId) => {
    await deleteTrip(tripId)
    invalidateAfterMutation()
  }

  return {
    trip,
    tripLoading,
    handleStartTrip,
    handleUpdateTrip,
    handleEndTrip,
    handleDeleteTrip,
  }
}
