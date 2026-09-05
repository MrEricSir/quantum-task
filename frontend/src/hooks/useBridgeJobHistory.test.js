import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup, act } from '@testing-library/react'
import { useBridgeJobHistory } from './useBridgeJobHistory'
import * as api from '../api'

vi.mock('../api', () => ({
  fetchBridgeJobHistory: vi.fn(),
}))

beforeEach(() => {
  vi.resetAllMocks()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useBridgeJobHistory', () => {
  it('fetches on mount and returns the jobs list', async () => {
    api.fetchBridgeJobHistory.mockResolvedValue([{ id: 1, status: 'done' }])
    const { result } = renderHook(() => useBridgeJobHistory(99))

    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.jobs).toEqual([{ id: 1, status: 'done' }]))
    expect(result.current.loading).toBe(false)
    expect(api.fetchBridgeJobHistory).toHaveBeenCalledWith(99)
  })

  it('starts with an empty list and silently ignores a failed fetch', async () => {
    api.fetchBridgeJobHistory.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useBridgeJobHistory(99))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.jobs).toEqual([])
  })

  it('does not poll once every job is terminal', async () => {
    vi.useFakeTimers()
    api.fetchBridgeJobHistory.mockResolvedValue([{ id: 1, status: 'done' }])
    renderHook(() => useBridgeJobHistory(99))

    await vi.waitFor(() => expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(1))
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(1)
  })

  it('polls on an interval while a job is pending or running', async () => {
    vi.useFakeTimers()
    api.fetchBridgeJobHistory.mockResolvedValue([{ id: 1, status: 'running' }])
    renderHook(() => useBridgeJobHistory(99))

    await vi.waitFor(() => expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(1))
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(3)
  })

  it('polls while a job needs confirmation', async () => {
    vi.useFakeTimers()
    api.fetchBridgeJobHistory.mockResolvedValue([{ id: 1, status: 'needs_confirmation' }])
    renderHook(() => useBridgeJobHistory(99))

    await vi.waitFor(() => expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(1))
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(2)
  })

  it('stops polling once unmounted', async () => {
    vi.useFakeTimers()
    api.fetchBridgeJobHistory.mockResolvedValue([{ id: 1, status: 'running' }])
    const { unmount } = renderHook(() => useBridgeJobHistory(99))
    await vi.waitFor(() => expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(1))

    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(1)
  })

  it('refetches when cardId changes', async () => {
    api.fetchBridgeJobHistory.mockResolvedValue([])
    const { rerender } = renderHook(({ cardId }) => useBridgeJobHistory(cardId), {
      initialProps: { cardId: 1 },
    })
    await waitFor(() => expect(api.fetchBridgeJobHistory).toHaveBeenCalledWith(1))

    rerender({ cardId: 2 })
    await waitFor(() => expect(api.fetchBridgeJobHistory).toHaveBeenCalledWith(2))
  })

  it('refresh() triggers an immediate refetch', async () => {
    api.fetchBridgeJobHistory.mockResolvedValue([])
    const { result } = renderHook(() => useBridgeJobHistory(99))
    await waitFor(() => expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(1))

    act(() => { result.current.refresh() })
    await waitFor(() => expect(api.fetchBridgeJobHistory).toHaveBeenCalledTimes(2))
  })
})
