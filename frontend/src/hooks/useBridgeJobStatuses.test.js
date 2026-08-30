import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup, act } from '@testing-library/react'
import { useBridgeJobStatuses } from './useBridgeJobStatuses'
import * as api from '../api'

vi.mock('../api', () => ({
  fetchBridgeJobStatuses: vi.fn(),
}))

beforeEach(() => {
  vi.resetAllMocks()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useBridgeJobStatuses', () => {
  it('does not fetch when not authed', () => {
    renderHook(() => useBridgeJobStatuses({ authed: false }))
    expect(api.fetchBridgeJobStatuses).not.toHaveBeenCalled()
  })

  it('fetches once on mount when authed and returns the map', async () => {
    api.fetchBridgeJobStatuses.mockResolvedValue({ 1: { job_id: 10, status: 'running' } })
    const { result } = renderHook(() => useBridgeJobStatuses({ authed: true }))

    await waitFor(() => expect(result.current.bridgeJobStatuses).toEqual({ 1: { job_id: 10, status: 'running' } }))
    expect(api.fetchBridgeJobStatuses).toHaveBeenCalledTimes(1)
  })

  it('starts with an empty map and silently ignores a failed fetch', async () => {
    api.fetchBridgeJobStatuses.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useBridgeJobStatuses({ authed: true }))

    await waitFor(() => expect(api.fetchBridgeJobStatuses).toHaveBeenCalled())
    expect(result.current.bridgeJobStatuses).toEqual({})
  })

  it('polls on an interval while authed', async () => {
    vi.useFakeTimers()
    api.fetchBridgeJobStatuses.mockResolvedValue({})
    renderHook(() => useBridgeJobStatuses({ authed: true }))

    await vi.waitFor(() => expect(api.fetchBridgeJobStatuses).toHaveBeenCalledTimes(1))

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(api.fetchBridgeJobStatuses).toHaveBeenCalledTimes(2)

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(api.fetchBridgeJobStatuses).toHaveBeenCalledTimes(3)
  })

  it('stops polling once no longer authed', async () => {
    vi.useFakeTimers()
    api.fetchBridgeJobStatuses.mockResolvedValue({})
    const { rerender } = renderHook(
      ({ authed }) => useBridgeJobStatuses({ authed }),
      { initialProps: { authed: true } }
    )
    await vi.waitFor(() => expect(api.fetchBridgeJobStatuses).toHaveBeenCalledTimes(1))

    rerender({ authed: false })
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(api.fetchBridgeJobStatuses).toHaveBeenCalledTimes(1)
  })
})
