import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchCalendarEvents } from '../api'

export const CALENDAR_QUERY_KEY = ['calendar']

export function useCalendar({ authed, invalidateBriefing }) {
  const queryClient = useQueryClient()

  const {
    data: calendarEvents = [],
    dataUpdatedAt,
    isFetching: calendarRefreshing,
    refetch,
  } = useQuery({
    queryKey: CALENDAR_QUERY_KEY,
    queryFn: fetchCalendarEvents,
    enabled: !!authed,
    refetchInterval: 15 * 60 * 1000,
  })

  const lastRefreshed = dataUpdatedAt ? new Date(dataUpdatedAt) : null

  const handleRefreshCalendar = async () => {
    try {
      await refetch()
      invalidateBriefing?.()
    } catch {
      // ignore
    }
  }

  return { calendarEvents, lastRefreshed, calendarRefreshing, handleRefreshCalendar }
}
