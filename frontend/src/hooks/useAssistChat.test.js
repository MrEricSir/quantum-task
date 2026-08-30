import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor, cleanup } from '@testing-library/react'
import { useAssistChat } from './useAssistChat'
import * as api from '../api'

vi.mock('../api', () => ({
  fetchCardThread: vi.fn(),
  sendThreadMessage: vi.fn(),
  saveThreadOutput: vi.fn(),
  updateThreadContext: vi.fn(),
  clearCardThread: vi.fn(),
  fetchContextFrom: vi.fn(),
}))

const task1 = { id: 1, title: 'Card one', tags: [] }
const task2 = { id: 2, title: 'Card two', tags: [] }

function makeSSEBody(rawLines) {
  const text = rawLines.join('\n')
  const bytes = new TextEncoder().encode(text)
  let sent = false
  return {
    getReader: () => ({
      read: vi.fn(async () => {
        if (sent) return { done: true, value: undefined }
        sent = true
        return { done: false, value: bytes }
      }),
    }),
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.fetchCardThread.mockResolvedValue({ messages: [], context: '', output: null })
  global.navigator.clipboard = { writeText: vi.fn(() => Promise.resolve()) }
  global.window.confirm = vi.fn(() => true)
})

afterEach(cleanup)

describe('useAssistChat — loading a thread', () => {
  it('does not fetch when closed', () => {
    renderHook(() => useAssistChat(task1, false, 'assist', true, vi.fn()))
    expect(api.fetchCardThread).not.toHaveBeenCalled()
  })

  it('does not fetch when there is no task', () => {
    renderHook(() => useAssistChat(null, true, 'assist', true, vi.fn()))
    expect(api.fetchCardThread).not.toHaveBeenCalled()
  })

  it('fetches and populates messages/context/output when opened for a task', async () => {
    api.fetchCardThread.mockResolvedValue({
      messages: [{ role: 'user', content: 'hi' }],
      context: 'some context',
      output: 'saved output',
    })
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))

    await waitFor(() => expect(result.current.messages).toHaveLength(1))
    expect(result.current.context).toBe('some context')
    expect(result.current.output).toBe('saved output')
    expect(api.fetchCardThread).toHaveBeenCalledWith(1)
  })

  it('falls back to empty state when the fetch fails', async () => {
    api.fetchCardThread.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))

    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())
    expect(result.current.messages).toEqual([])
    expect(result.current.output).toBeNull()
  })

  it('refetches and resets when the task changes', async () => {
    api.fetchCardThread.mockResolvedValueOnce({ messages: [{ role: 'user', content: 'first' }], context: '', output: null })
    const { result, rerender } = renderHook(
      ({ task }) => useAssistChat(task, true, 'assist', true, vi.fn()),
      { initialProps: { task: task1 } }
    )
    await waitFor(() => expect(result.current.messages).toHaveLength(1))

    api.fetchCardThread.mockResolvedValueOnce({ messages: [{ role: 'user', content: 'second' }], context: '', output: null })
    rerender({ task: task2 })

    await waitFor(() => expect(result.current.messages[0]?.content).toBe('second'))
    expect(api.fetchCardThread).toHaveBeenCalledWith(2)
  })
})

describe('useAssistChat — context panel', () => {
  it('handleToggleContext opens the panel and seeds the editor from context', async () => {
    api.fetchCardThread.mockResolvedValue({ messages: [], context: 'existing', output: null })
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(result.current.context).toBe('existing'))

    act(() => result.current.handleToggleContext())
    expect(result.current.showContext).toBe(true)
    expect(result.current.editContext).toBe('existing')

    act(() => result.current.handleToggleContext())
    expect(result.current.showContext).toBe(false)
  })

  it('handleCancelContext closes the panel and discards edits', async () => {
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    act(() => result.current.handleToggleContext())
    act(() => result.current.setEditContext('unsaved draft'))
    act(() => result.current.handleCancelContext())

    expect(result.current.showContext).toBe(false)
    expect(result.current.editContext).toBe(result.current.context)
  })

  it('saveContext persists the draft and closes the panel', async () => {
    api.updateThreadContext.mockResolvedValue(undefined)
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    act(() => result.current.setEditContext('new context'))
    await act(() => result.current.saveContext())

    expect(api.updateThreadContext).toHaveBeenCalledWith(1, 'new context')
    expect(result.current.context).toBe('new context')
    expect(result.current.showContext).toBe(false)
  })

  it('loadContextFrom populates the editor and a summary label', async () => {
    api.fetchContextFrom.mockResolvedValue({ context_text: 'loaded text', count: 3, label: 'Work tag' })
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    await act(() => result.current.loadContextFrom({ target: { value: 'tag:5' } }))

    expect(api.fetchContextFrom).toHaveBeenCalledWith(1, 'tag', { section: undefined, tagId: 5 })
    expect(result.current.editContext).toBe('loaded text')
    expect(result.current.ctxLoadedFrom).toBe('3 cards from Work tag')
  })

  it('loadContextFrom reports when no cards are found', async () => {
    api.fetchContextFrom.mockResolvedValue({ context_text: '', count: 0, label: 'Today' })
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    await act(() => result.current.loadContextFrom({ target: { value: 'section:today' } }))

    expect(result.current.ctxLoadedFrom).toBe('No cards found in Today')
  })
})

describe('useAssistChat — saved output', () => {
  it('handleSaveOutput persists and calls onOutputSaved', async () => {
    const onOutputSaved = vi.fn()
    api.saveThreadOutput.mockResolvedValue(undefined)
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, onOutputSaved))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    await act(() => result.current.handleSaveOutput('great answer', 2))

    expect(api.saveThreadOutput).toHaveBeenCalledWith(1, 'great answer')
    expect(result.current.output).toBe('great answer')
    expect(onOutputSaved).toHaveBeenCalledWith('great answer')
  })

  it('handleClearOutput does nothing without confirmation', async () => {
    global.window.confirm = vi.fn(() => false)
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    await act(() => result.current.handleClearOutput())
    expect(api.saveThreadOutput).not.toHaveBeenCalled()
  })

  it('handleClearOutput clears output when confirmed', async () => {
    const onOutputSaved = vi.fn()
    api.saveThreadOutput.mockResolvedValue(undefined)
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, onOutputSaved))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    await act(() => result.current.handleClearOutput())

    expect(api.saveThreadOutput).toHaveBeenCalledWith(1, null)
    expect(result.current.output).toBeNull()
    expect(onOutputSaved).toHaveBeenCalledWith(null)
  })

  it('handleCopyOutput and handleCopy write to the clipboard', async () => {
    api.fetchCardThread.mockResolvedValue({ messages: [], context: '', output: 'saved text' })
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(result.current.output).toBe('saved text'))

    act(() => result.current.handleCopyOutput())
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('saved text')

    act(() => result.current.handleCopy('a message', 0))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('a message')
  })
})

describe('useAssistChat — clear thread', () => {
  it('does nothing without confirmation', async () => {
    global.window.confirm = vi.fn(() => false)
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    await act(() => result.current.handleClearThread())
    expect(api.clearCardThread).not.toHaveBeenCalled()
  })

  it('clears messages/output/context when confirmed', async () => {
    const onOutputSaved = vi.fn()
    api.fetchCardThread.mockResolvedValue({ messages: [{ role: 'user', content: 'hi' }], context: 'ctx', output: 'out' })
    api.clearCardThread.mockResolvedValue(undefined)
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, onOutputSaved))
    await waitFor(() => expect(result.current.messages).toHaveLength(1))

    await act(() => result.current.handleClearThread())

    expect(api.clearCardThread).toHaveBeenCalledWith(1)
    expect(result.current.messages).toEqual([])
    expect(result.current.output).toBeNull()
    expect(result.current.context).toBe('')
    expect(onOutputSaved).toHaveBeenCalledWith(null)
  })
})

describe('useAssistChat — send', () => {
  it('streams an assistant reply and appends it to the thread', async () => {
    api.sendThreadMessage.mockResolvedValue({
      ok: true,
      body: makeSSEBody([
        'data: {"text":"Hello "}',
        'data: {"text":"there"}',
        'data: [DONE]',
        '',
      ]),
    })
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    act(() => result.current.setInput('hi there'))
    await act(() => result.current.send())

    await waitFor(() => {
      const last = result.current.messages[result.current.messages.length - 1]
      expect(last.content).toBe('Hello there')
      expect(last._streaming).toBe(false)
    })
    expect(result.current.messages[0]).toMatchObject({ role: 'user', content: 'hi there' })
    expect(result.current.sending).toBe(false)
    expect(result.current.streaming).toBe(false)
  })

  it('does not send an empty message', async () => {
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    await act(() => result.current.send())
    expect(api.sendThreadMessage).not.toHaveBeenCalled()
  })

  it('surfaces a thread error and drops the placeholder when the request fails', async () => {
    api.sendThreadMessage.mockRejectedValue(new Error('network down'))
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())

    act(() => result.current.setInput('hi there'))
    await act(() => result.current.send())

    expect(result.current.threadErr).toBe('Could not reach the assistant.')
    expect(result.current.messages.every(m => !m._streaming)).toBe(true)
  })

  it('handleKeyDown sends on Enter but not Shift+Enter', async () => {
    api.sendThreadMessage.mockResolvedValue({ ok: true, body: makeSSEBody(['data: [DONE]', '']) })
    const { result } = renderHook(() => useAssistChat(task1, true, 'assist', true, vi.fn()))
    await waitFor(() => expect(api.fetchCardThread).toHaveBeenCalled())
    act(() => result.current.setInput('hey'))

    const shiftEnter = { key: 'Enter', shiftKey: true, preventDefault: vi.fn() }
    act(() => result.current.handleKeyDown(shiftEnter))
    expect(api.sendThreadMessage).not.toHaveBeenCalled()

    const plainEnter = { key: 'Enter', shiftKey: false, preventDefault: vi.fn() }
    await act(() => result.current.handleKeyDown(plainEnter))
    expect(plainEnter.preventDefault).toHaveBeenCalled()
    expect(api.sendThreadMessage).toHaveBeenCalled()
  })
})
