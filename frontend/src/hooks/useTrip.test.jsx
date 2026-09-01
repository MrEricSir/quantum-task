import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useTrip } from './useTrip'
import * as api from '../api'

vi.mock('../api', () => ({
  fetchTrip: vi.fn(),
  startTrip: vi.fn(),
  updateTrip: vi.fn(),
  endTrip: vi.fn(),
  deleteTrip: vi.fn(),
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
})

describe('useTrip', () => {
  it('does not fetch when not authed', () => {
    renderHook(() => useTrip({ authed: false }), { wrapper })
    expect(api.fetchTrip).not.toHaveBeenCalled()
  })

  it('fetches the active trip on mount when authed', async () => {
    api.fetchTrip.mockResolvedValue({ id: 1, name: 'Tokyo', start_date: '2026-09-01', end_date: null })
    const { result } = renderHook(() => useTrip({ authed: true }), { wrapper })
    await waitFor(() => expect(result.current.trip).toEqual({
      id: 1, name: 'Tokyo', start_date: '2026-09-01', end_date: null,
    }))
  })

  it('defaults to null when there is no trip', async () => {
    api.fetchTrip.mockResolvedValue(null)
    const { result } = renderHook(() => useTrip({ authed: true }), { wrapper })
    await waitFor(() => expect(result.current.tripLoading).toBe(false))
    expect(result.current.trip).toBeNull()
  })

  it('starting a trip refetches trip and habits', async () => {
    api.fetchTrip.mockResolvedValue(null)
    api.startTrip.mockResolvedValue({ id: 1, name: null, start_date: '2026-09-01', end_date: null })
    const { result } = renderHook(() => useTrip({ authed: true }), { wrapper })
    await waitFor(() => expect(result.current.tripLoading).toBe(false))

    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    await act(async () => { await result.current.handleStartTrip(null, '2026-09-01') })

    expect(api.startTrip).toHaveBeenCalledWith(null, '2026-09-01')
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['trip'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['habits'] })
  })

  it('ending a trip returns the retrospective result and invalidates caches', async () => {
    api.fetchTrip.mockResolvedValue({ id: 1, name: null, start_date: '2026-09-01', end_date: null })
    api.endTrip.mockResolvedValue({
      trip: { id: 1, name: null, start_date: '2026-09-01', end_date: '2026-09-06', retrospective_sent: true },
      retrospective: '<b>Welcome back</b>',
    })
    const { result } = renderHook(() => useTrip({ authed: true }), { wrapper })
    await waitFor(() => expect(result.current.tripLoading).toBe(false))

    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    let outcome
    await act(async () => { outcome = await result.current.handleEndTrip(1) })

    expect(api.endTrip).toHaveBeenCalledWith(1)
    expect(outcome.retrospective).toBe('<b>Welcome back</b>')
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['trip'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['habits'] })
  })

  it('deleting a trip invalidates caches', async () => {
    api.fetchTrip.mockResolvedValue({ id: 1, name: null, start_date: '2026-09-01', end_date: null })
    api.deleteTrip.mockResolvedValue(undefined)
    const { result } = renderHook(() => useTrip({ authed: true }), { wrapper })
    await waitFor(() => expect(result.current.tripLoading).toBe(false))

    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    await act(async () => { await result.current.handleDeleteTrip(1) })

    expect(api.deleteTrip).toHaveBeenCalledWith(1)
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['trip'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['habits'] })
  })
})
