import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, cleanup, act } from '@testing-library/react'
import useLoadingStage from './useLoadingStage'

const STAGES = [
  { after: 0, text: '' },
  { after: 1000, text: 'slow' },
  { after: 3000, text: 'very slow' },
]

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useLoadingStage', () => {
  it('starts at the first stage text while inactive', () => {
    const { result } = renderHook(() => useLoadingStage(false, STAGES))
    expect(result.current).toBe('')
  })

  it('escalates through later stages the longer active stays true', () => {
    const { result } = renderHook(() => useLoadingStage(true, STAGES))
    expect(result.current).toBe('')

    act(() => vi.advanceTimersByTime(1000))
    expect(result.current).toBe('slow')

    act(() => vi.advanceTimersByTime(2000))
    expect(result.current).toBe('very slow')
  })

  it('resets to the first stage when active goes back to false', () => {
    const { result, rerender } = renderHook(({ active }) => useLoadingStage(active, STAGES), {
      initialProps: { active: true },
    })
    act(() => vi.advanceTimersByTime(3000))
    expect(result.current).toBe('very slow')

    rerender({ active: false })
    expect(result.current).toBe('')
  })

  it('starts over from the first stage on a fresh active period', () => {
    const { result, rerender } = renderHook(({ active }) => useLoadingStage(active, STAGES), {
      initialProps: { active: true },
    })
    act(() => vi.advanceTimersByTime(3000))
    expect(result.current).toBe('very slow')

    rerender({ active: false })
    rerender({ active: true })
    expect(result.current).toBe('')

    act(() => vi.advanceTimersByTime(1000))
    expect(result.current).toBe('slow')
  })

  it('does not fire timers after unmount', () => {
    const { result, unmount } = renderHook(() => useLoadingStage(true, STAGES))
    unmount()
    expect(() => act(() => vi.advanceTimersByTime(5000))).not.toThrow()
  })
})
