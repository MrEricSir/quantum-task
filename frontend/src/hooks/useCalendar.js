import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchCalendarEvents } from '../api'

export const CALENDAR_QUERY_KEY = ['calendar']

export function useCalendar({ authed, invalidateBriefing }) {
  const queryClient = useQueryClient()

  const {
    data: calendarEvents = [],
    dataUpdatedAt,
    isLoading: calendarLoading,
    isFetching: calendarRefreshing,
    refetch,
  } = useQuery({
    queryKey: CALENDAR_QUERY_KEY,
    queryFn: fetchCalendarEvents,
    enabled: !!authed,
    staleTime: 10 * 60 * 1000,   // treat cached data as fresh for 10 min
    refetchInterval: 15 * 60 * 1000,
  })

  const lastRefreshed = dataUpdatedAt ? new Date(dataUpdatedAt) : null

  const handleRefreshCalendar = async () => {
    try {
      await queryClient.fetchQuery({
        queryKey: CALENDAR_QUERY_KEY,
        queryFn: () => fetchCalendarEvents({ force: true }),
        staleTime: 0,
      })
      invalidateBriefing?.()
    } catch {
      // ignore
    }
  }

  return { calendarEvents, calendarLoading, lastRefreshed, calendarRefreshing, handleRefreshCalendar }
}
