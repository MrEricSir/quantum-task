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
    // Without this, refetchInterval pauses the instant the tab loses focus (React Query's
    // default) -- a tab left open in the background for hours would otherwise come back
    // showing whatever was cached from before it was backgrounded, not just 15-min stale.
    refetchIntervalInBackground: true,
  })

  const isStale = () => !dataUpdatedAt || Date.now() - dataUpdatedAt > STALE_TIME_MS

  // This query lives at the App shell level, not inside CalendarPage, so it never
  // remounts on in-app navigation to /calendar. Without this, arriving at Calendar after
  // being on another page for a while (longer than staleTime, shorter than the background
  // poll) silently shows stale events until a manual refresh.
  useEffect(() => {
    if (!active) return
    if (isStale()) refetch()
  }, [active]) // eslint-disable-line react-hooks/exhaustive-deps

  // Belt-and-suspenders for a backgrounded tab or OS sleep: React Query's own
  // refetchOnWindowFocus is supposed to cover this, but relying solely on it left stale
  // data on return in practice (confirmed by user report) -- an explicit listener doesn't
  // depend on trusting that mechanism fires reliably in every browser/PWA context.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && isStale()) refetch()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [dataUpdatedAt]) // eslint-disable-line react-hooks/exhaustive-deps

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
