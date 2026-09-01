import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchCalendarEvents } from '../api'

export const CALENDAR_QUERY_KEY = ['calendar']
const STALE_TIME_MS = 10 * 60 * 1000   // treat cached data as fresh for 10 min

export function useCalendar({ authed, invalidateBriefing, active = false }) {
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
    staleTime: STALE_TIME_MS,
    refetchInterval: 15 * 60 * 1000,
  })

  // This query lives at the App shell level, not inside CalendarPage, so it never
  // remounts on in-app navigation to /calendar -- refetchOnWindowFocus only fires on
  // real browser tab focus/blur, which doesn't happen when someone just clicks between
  // pages in the SPA. Without this, arriving at Calendar after being on another page for
  // a while (longer than staleTime, shorter than the 15-min background poll) silently
  // shows stale events until a manual refresh.
  useEffect(() => {
    // dataUpdatedAt is falsy until the query's own initial fetch resolves -- only step in
    // for a previously-loaded query that's since gone stale, never race that first fetch.
    if (!active || !dataUpdatedAt) return
    if (Date.now() - dataUpdatedAt > STALE_TIME_MS) refetch()
  }, [active]) // eslint-disable-line react-hooks/exhaustive-deps

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
