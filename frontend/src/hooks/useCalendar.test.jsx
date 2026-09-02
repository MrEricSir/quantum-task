import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useCalendar } from './useCalendar'
import * as api from '../api'

vi.mock('../api', () => ({
  fetchCalendarEvents: vi.fn(),
}))

let queryClient

function wrapper({ children }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.resetAllMocks()
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(() => {
  cleanup()
  // A prior test may have redefined this to 'hidden' -- reset so later tests (and the
  // window-focus handler's own visibilityState check) start from a known-good state.
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
})

describe('useCalendar', () => {
  it('does not fetch when not authed', () => {
    renderHook(() => useCalendar({ authed: false, invalidateBriefing: vi.fn() }), { wrapper })
    expect(api.fetchCalendarEvents).not.toHaveBeenCalled()
  })

  it('fetches once on mount when authed', async () => {
    api.fetchCalendarEvents.mockResolvedValue([{ id: 1 }])
    const { result } = renderHook(
      () => useCalendar({ authed: true, invalidateBriefing: vi.fn() }),
      { wrapper }
    )
    await waitFor(() => expect(result.current.calendarEvents).toEqual([{ id: 1 }]))
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)
  })

  it('does not refetch just from becoming active when data is still fresh', async () => {
    api.fetchCalendarEvents.mockResolvedValue([])
    const { result, rerender } = renderHook(
      ({ active }) => useCalendar({ authed: true, invalidateBriefing: vi.fn(), active }),
      { wrapper, initialProps: { active: false } }
    )
    await waitFor(() => expect(result.current.lastRefreshed).toBeTruthy())
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)

    rerender({ active: true })
    // No new fetch -- freshly loaded, well within staleTime.
    await new Promise((r) => setTimeout(r, 10))
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)
  })

  it('refetches when navigating to an active state with stale data', async () => {
    api.fetchCalendarEvents.mockResolvedValue([])
    const dateSpy = vi.spyOn(Date, 'now')
    dateSpy.mockReturnValue(1_000_000)

    const { result, rerender } = renderHook(
      ({ active }) => useCalendar({ authed: true, invalidateBriefing: vi.fn(), active }),
      { wrapper, initialProps: { active: false } }
    )
    await waitFor(() => expect(result.current.lastRefreshed).toBeTruthy())
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)

    // Simulate coming back to the Calendar page 11 minutes later (staleTime is 10 min).
    dateSpy.mockReturnValue(1_000_000 + 11 * 60 * 1000)
    rerender({ active: true })

    await waitFor(() => expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(2))
    dateSpy.mockRestore()
  })

  it('refetches when the tab regains visibility with stale data', async () => {
    api.fetchCalendarEvents.mockResolvedValue([])
    const dateSpy = vi.spyOn(Date, 'now')
    dateSpy.mockReturnValue(1_000_000)

    const { result } = renderHook(
      () => useCalendar({ authed: true, invalidateBriefing: vi.fn() }),
      { wrapper }
    )
    await waitFor(() => expect(result.current.lastRefreshed).toBeTruthy())
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)

    // Simulate the tab having been backgrounded for a while and coming back.
    dateSpy.mockReturnValue(1_000_000 + 11 * 60 * 1000)
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))

    await waitFor(() => expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(2))
    dateSpy.mockRestore()
  })

  it('does not refetch on visibility change when data is still fresh', async () => {
    api.fetchCalendarEvents.mockResolvedValue([])
    const { result } = renderHook(
      () => useCalendar({ authed: true, invalidateBriefing: vi.fn() }),
      { wrapper }
    )
    await waitFor(() => expect(result.current.lastRefreshed).toBeTruthy())
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((r) => setTimeout(r, 10))
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)
  })

  it('ignores a visibilitychange event while the tab is actually hidden', async () => {
    api.fetchCalendarEvents.mockResolvedValue([])
    const dateSpy = vi.spyOn(Date, 'now')
    dateSpy.mockReturnValue(1_000_000)

    const { result } = renderHook(
      () => useCalendar({ authed: true, invalidateBriefing: vi.fn() }),
      { wrapper }
    )
    await waitFor(() => expect(result.current.lastRefreshed).toBeTruthy())
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)

    dateSpy.mockReturnValue(1_000_000 + 11 * 60 * 1000)
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((r) => setTimeout(r, 10))
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)
    dateSpy.mockRestore()
  })

  it('refetches on window focus when data is stale', async () => {
    api.fetchCalendarEvents.mockResolvedValue([])
    const dateSpy = vi.spyOn(Date, 'now')
    dateSpy.mockReturnValue(1_000_000)

    const { result } = renderHook(
      () => useCalendar({ authed: true, invalidateBriefing: vi.fn() }),
      { wrapper }
    )
    await waitFor(() => expect(result.current.lastRefreshed).toBeTruthy())
    expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(1)

    dateSpy.mockReturnValue(1_000_000 + 11 * 60 * 1000)
    window.dispatchEvent(new Event('focus'))

    await waitFor(() => expect(api.fetchCalendarEvents).toHaveBeenCalledTimes(2))
    dateSpy.mockRestore()
  })
})
