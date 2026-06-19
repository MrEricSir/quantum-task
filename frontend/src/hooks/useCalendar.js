import { useState, useEffect } from 'react'
import { fetchCalendarEvents } from '../api'

export function useCalendar({ authed, invalidateBriefing }) {
  const [calendarEvents, setCalendarEvents] = useState([])
  const [lastRefreshed, setLastRefreshed] = useState(null)
  const [calendarRefreshing, setCalendarRefreshing] = useState(false)

  useEffect(() => {
    if (!authed) return
    fetchCalendarEvents()
      .then((events) => { setCalendarEvents(events); setLastRefreshed(new Date()) })
      .catch(() => {})
  }, [authed])

  // Poll for calendar updates every 15 minutes
  useEffect(() => {
    const id = setInterval(() => {
      fetchCalendarEvents()
        .then((events) => { setCalendarEvents(events); setLastRefreshed(new Date()) })
        .catch(() => {})
    }, 15 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  const handleRefreshCalendar = async () => {
    setCalendarRefreshing(true)
    try {
      const events = await fetchCalendarEvents()
      setCalendarEvents(events)
      setLastRefreshed(new Date())
      invalidateBriefing?.()
    } catch {
      // ignore
    } finally {
      setCalendarRefreshing(false)
    }
  }

  return { calendarEvents, lastRefreshed, calendarRefreshing, handleRefreshCalendar }
}
