import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor, cleanup } from '@testing-library/react'
import { useAssistBreakdown } from './useAssistBreakdown'
import * as api from '../api'

vi.mock('../api', () => ({
  breakdownCard: vi.fn(),
  commitBreakdown: vi.fn(),
}))

const task = { id: 7, title: 'Big task' }

beforeEach(() => {
  vi.resetAllMocks()
  global.window.confirm = vi.fn(() => true)
})

afterEach(cleanup)

describe('useAssistBreakdown — auto-generation on entering the tab', () => {
  it('does not generate until mode is "breakdown"', () => {
    renderHook(({ mode }) => useAssistBreakdown(task, true, 'assist', mode, vi.fn(), vi.fn()), {
      initialProps: { mode: 'assist' },
    })
    expect(api.breakdownCard).not.toHaveBeenCalled()
  })

  it('generates subtasks once mode becomes "breakdown"', async () => {
    api.breakdownCard.mockResolvedValue({ subtasks: ['Step one', 'Step two'], tag_name: 'big-task' })
    const { result, rerender } = renderHook(
      ({ mode }) => useAssistBreakdown(task, true, 'assist', mode, vi.fn(), vi.fn()),
      { initialProps: { mode: 'assist' } }
    )
    rerender({ mode: 'breakdown' })

    await waitFor(() => expect(result.current.bdStatus).toBe('ready'))
    expect(result.current.bdSubtasks).toEqual(['Step one', 'Step two'])
    expect(result.current.bdTagName).toBe('big-task')
    expect(api.breakdownCard).toHaveBeenCalledWith(7)
  })

  it('does not regenerate on repeated renders once ready', async () => {
    api.breakdownCard.mockResolvedValue({ subtasks: ['One'], tag_name: 'tag' })
    const { result, rerender } = renderHook(
      ({ mode }) => useAssistBreakdown(task, true, 'assist', mode, vi.fn(), vi.fn()),
      { initialProps: { mode: 'breakdown' } }
    )
    await waitFor(() => expect(result.current.bdStatus).toBe('ready'))

    rerender({ mode: 'breakdown' })
    expect(api.breakdownCard).toHaveBeenCalledTimes(1)
  })

  it('sets an error status when generation fails', async () => {
    api.breakdownCard.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useAssistBreakdown(task, true, 'assist', 'breakdown', vi.fn(), vi.fn()))

    await waitFor(() => expect(result.current.bdStatus).toBe('error'))
    expect(result.current.bdError).toBe('Failed to generate subtasks.')
  })
})

describe('useAssistBreakdown — reset on open', () => {
  it('resets state when the task changes while open', async () => {
    api.breakdownCard.mockResolvedValue({ subtasks: ['A'], tag_name: 'tag-a' })
    const { result, rerender } = renderHook(
      ({ task }) => useAssistBreakdown(task, true, 'assist', 'breakdown', vi.fn(), vi.fn()),
      { initialProps: { task } }
    )
    await waitFor(() => expect(result.current.bdStatus).toBe('ready'))

    rerender({ task: { id: 8, title: 'Other task' } })

    // Matches AssistModal's original behavior: the auto-generate effect only depends on
    // `mode`, not `task?.id`, so switching tasks while staying on the breakdown tab resets
    // to 'idle' without automatically regenerating (this only ever happens in practice when
    // the whole panel remounts fresh for a new card, which re-initializes mode too).
    expect(result.current.bdStatus).toBe('idle')
    expect(result.current.bdSubtasks).toEqual([])
  })
})

describe('useAssistBreakdown — confirmBreakdown', () => {
  it('does nothing when there are no valid (non-blank) subtasks', async () => {
    api.breakdownCard.mockResolvedValue({ subtasks: ['   ', ''], tag_name: 'tag' })
    const { result } = renderHook(() => useAssistBreakdown(task, true, 'assist', 'breakdown', vi.fn(), vi.fn()))
    await waitFor(() => expect(result.current.bdStatus).toBe('ready'))

    await act(() => result.current.confirmBreakdown())
    expect(api.commitBreakdown).not.toHaveBeenCalled()
  })

  it('commits valid subtasks and calls onBreakdown + handleClose', async () => {
    const onBreakdown = vi.fn()
    const handleClose = vi.fn()
    api.breakdownCard.mockResolvedValue({ subtasks: ['Step one', ''], tag_name: 'big-task' })
    api.commitBreakdown.mockResolvedValue({ ok: true })
    const { result } = renderHook(() => useAssistBreakdown(task, true, 'assist', 'breakdown', onBreakdown, handleClose))
    await waitFor(() => expect(result.current.bdStatus).toBe('ready'))

    await act(() => result.current.confirmBreakdown())

    expect(api.commitBreakdown).toHaveBeenCalledWith(7, ['Step one'], 'big-task')
    expect(onBreakdown).toHaveBeenCalledWith({ ok: true })
    expect(handleClose).toHaveBeenCalled()
  })

  it('sets an error and stays ready when committing fails', async () => {
    const handleClose = vi.fn()
    api.breakdownCard.mockResolvedValue({ subtasks: ['Step one'], tag_name: 'big-task' })
    api.commitBreakdown.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useAssistBreakdown(task, true, 'assist', 'breakdown', vi.fn(), handleClose))
    await waitFor(() => expect(result.current.bdStatus).toBe('ready'))

    await act(() => result.current.confirmBreakdown())

    expect(result.current.bdStatus).toBe('ready')
    expect(result.current.bdError).toBe('Failed to create subtasks.')
    expect(handleClose).not.toHaveBeenCalled()
  })
})
