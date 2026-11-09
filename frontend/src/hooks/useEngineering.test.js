import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup, act } from '@testing-library/react'
import { useEngineering } from './useEngineering'
import * as api from '../api'

vi.mock('../api', () => ({
  syncEngineering: vi.fn(),
  fetchEngineeringItems: vi.fn(),
}))

function setVisibility(state) {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  vi.resetAllMocks()
  setVisibility('visible')
  api.syncEngineering.mockResolvedValue()
  api.fetchEngineeringItems.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useEngineering', () => {
  it('does not sync when not authed', () => {
    renderHook(() => useEngineering({ authed: false }))
    expect(api.syncEngineering).not.toHaveBeenCalled()
  })

  it('syncs once on mount when authed', async () => {
    renderHook(() => useEngineering({ authed: true }))
    await waitFor(() => expect(api.syncEngineering).toHaveBeenCalledTimes(1))
  })

  it('polls on a 15-minute interval while authed and visible', async () => {
    vi.useFakeTimers()
    renderHook(() => useEngineering({ authed: true }))

    await vi.waitFor(() => expect(api.syncEngineering).toHaveBeenCalledTimes(1))

    await act(async () => { await vi.advanceTimersByTimeAsync(15 * 60 * 1000) })
    expect(api.syncEngineering).toHaveBeenCalledTimes(2)
  })

  it('does not poll while the tab is hidden', async () => {
    vi.useFakeTimers()
    setVisibility('hidden')
    renderHook(() => useEngineering({ authed: true }))

    await vi.waitFor(() => expect(api.syncEngineering).toHaveBeenCalledTimes(1))

    await act(async () => { await vi.advanceTimersByTimeAsync(60 * 60 * 1000) })
    expect(api.syncEngineering).toHaveBeenCalledTimes(1)
  })

  it('refreshes immediately on regaining visibility and resumes polling', async () => {
    vi.useFakeTimers()
    renderHook(() => useEngineering({ authed: true }))
    await vi.waitFor(() => expect(api.syncEngineering).toHaveBeenCalledTimes(1))

    await act(async () => { setVisibility('hidden') })
    await act(async () => { await vi.advanceTimersByTimeAsync(20 * 60 * 1000) })
    expect(api.syncEngineering).toHaveBeenCalledTimes(1)

    await act(async () => { setVisibility('visible') })
    expect(api.syncEngineering).toHaveBeenCalledTimes(2)

    await act(async () => { await vi.advanceTimersByTimeAsync(15 * 60 * 1000) })
    expect(api.syncEngineering).toHaveBeenCalledTimes(3)
  })

  it('stops polling once no longer authed', async () => {
    vi.useFakeTimers()
    const { rerender } = renderHook(
      ({ authed }) => useEngineering({ authed }),
      { initialProps: { authed: true } },
    )
    await vi.waitFor(() => expect(api.syncEngineering).toHaveBeenCalledTimes(1))

    rerender({ authed: false })
    await act(async () => { await vi.advanceTimersByTimeAsync(60 * 60 * 1000) })
    expect(api.syncEngineering).toHaveBeenCalledTimes(1)
  })
})
