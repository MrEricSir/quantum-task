import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup, act } from '@testing-library/react'
import { useBridgeJobsDashboard } from './useBridgeJobsDashboard'
import * as api from '../api'

vi.mock('../api', () => ({
  fetchBridgeJobsDashboard: vi.fn(),
}))

beforeEach(() => {
  vi.resetAllMocks()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useBridgeJobsDashboard', () => {
  it('fetches on mount and returns the jobs list', async () => {
    api.fetchBridgeJobsDashboard.mockResolvedValue([{ id: 1, status: 'running' }])
    const { result } = renderHook(() => useBridgeJobsDashboard())

    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.jobs).toEqual([{ id: 1, status: 'running' }]))
    expect(result.current.loading).toBe(false)
  })

  it('starts with an empty list and silently ignores a failed fetch', async () => {
    api.fetchBridgeJobsDashboard.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useBridgeJobsDashboard())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.jobs).toEqual([])
  })

  it('polls on an interval', async () => {
    vi.useFakeTimers()
    api.fetchBridgeJobsDashboard.mockResolvedValue([])
    renderHook(() => useBridgeJobsDashboard())

    await vi.waitFor(() => expect(api.fetchBridgeJobsDashboard).toHaveBeenCalledTimes(1))

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(api.fetchBridgeJobsDashboard).toHaveBeenCalledTimes(2)

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(api.fetchBridgeJobsDashboard).toHaveBeenCalledTimes(3)
  })

  it('stops polling once unmounted', async () => {
    vi.useFakeTimers()
    api.fetchBridgeJobsDashboard.mockResolvedValue([])
    const { unmount } = renderHook(() => useBridgeJobsDashboard())
    await vi.waitFor(() => expect(api.fetchBridgeJobsDashboard).toHaveBeenCalledTimes(1))

    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(api.fetchBridgeJobsDashboard).toHaveBeenCalledTimes(1)
  })
})
