import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor, cleanup } from '@testing-library/react'
import { useAssistCode } from './useAssistCode'
import * as api from '../api'

vi.mock('../api', () => ({
  generateSpec: vi.fn(),
  queueBridgeJob: vi.fn(),
  getBridgeJob: vi.fn(),
  getBridgeJobChain: vi.fn(),
  queueResumeJob: vi.fn(),
  queueCompanionJob: vi.fn(),
  getKnownBridgeRepos: vi.fn(),
}))

const taskNoSpec = { id: 3, title: 'Ship the thing', spec: null }
const taskWithSpec = { id: 4, title: 'Ship the other thing', spec: 'Do the work' }

beforeEach(() => {
  vi.resetAllMocks()
  api.getBridgeJobChain.mockResolvedValue({ root: null, companion: null })
  api.getKnownBridgeRepos.mockResolvedValue([])
  global.navigator.clipboard = { writeText: vi.fn(() => Promise.resolve()) }
})

afterEach(cleanup)

describe('useAssistCode — loading on open', () => {
  it('does not fetch the job chain when the task has no spec yet', () => {
    const { result } = renderHook(() => useAssistCode(taskNoSpec, true, 'code', vi.fn()))
    expect(result.current.specText).toBeNull()
    expect(api.getBridgeJobChain).not.toHaveBeenCalled()
  })

  it('seeds specText from the task and fetches the job chain when a spec exists', async () => {
    api.getBridgeJobChain.mockResolvedValue({ root: { id: 1, status: 'done' }, companion: null })
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))

    expect(result.current.specText).toBe('Do the work')
    await waitFor(() => expect(result.current.bridgeJob).toEqual({ id: 1, status: 'done' }))
    expect(api.getBridgeJobChain).toHaveBeenCalledWith(4)
  })

  it('does nothing when closed', () => {
    renderHook(() => useAssistCode(taskWithSpec, false, 'code', vi.fn()))
    expect(api.getBridgeJobChain).not.toHaveBeenCalled()
  })

  it('resets bridge/companion state when the task changes', async () => {
    api.getBridgeJobChain.mockResolvedValue({ root: { id: 1, status: 'done' }, companion: null })
    const { result, rerender } = renderHook(
      ({ task }) => useAssistCode(task, true, 'code', vi.fn()),
      { initialProps: { task: taskWithSpec } }
    )
    await waitFor(() => expect(result.current.bridgeJob).not.toBeNull())

    rerender({ task: taskNoSpec })
    expect(result.current.bridgeJob).toBeNull()
    expect(result.current.specText).toBeNull()
  })

  it('computes a slugified default branch name from the task title', () => {
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    expect(result.current.defaultBranch).toBe('qtask/4-ship-the-other-thing')
  })
})

describe('useAssistCode — spec generation', () => {
  it('generates a spec and calls onSpecSaved', async () => {
    const onSpecSaved = vi.fn()
    api.generateSpec.mockResolvedValue({ spec: 'Generated brief' })
    const { result } = renderHook(() => useAssistCode(taskNoSpec, true, 'code', onSpecSaved))

    await act(() => result.current.handleGenerateSpec())

    expect(api.generateSpec).toHaveBeenCalledWith(3)
    expect(result.current.specText).toBe('Generated brief')
    expect(onSpecSaved).toHaveBeenCalledWith('Generated brief')
    expect(result.current.specGenerating).toBe(false)
  })

  it('sets an error when generation fails', async () => {
    api.generateSpec.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useAssistCode(taskNoSpec, true, 'code', vi.fn()))

    await act(() => result.current.handleGenerateSpec())

    expect(result.current.specError).toBe('Failed to generate brief. Please try again.')
  })

  it('handleStartSpecEdit/handleSaveSpec/handleCancelSpecEdit manage the draft', async () => {
    const onSpecSaved = vi.fn()
    api.getBridgeJobChain.mockResolvedValue({ root: null, companion: null })
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', onSpecSaved))
    await waitFor(() => expect(api.getBridgeJobChain).toHaveBeenCalled())

    act(() => result.current.handleStartSpecEdit())
    expect(result.current.specEditing).toBe(true)
    expect(result.current.specDraft).toBe('Do the work')

    act(() => result.current.setSpecDraft('Edited brief'))
    act(() => result.current.handleSaveSpec())

    expect(result.current.specText).toBe('Edited brief')
    expect(result.current.specEditing).toBe(false)
    expect(onSpecSaved).toHaveBeenCalledWith('Edited brief')
  })

  it('handleCancelSpecEdit discards edits without confirmation when unchanged', () => {
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    act(() => result.current.handleStartSpecEdit())
    act(() => result.current.handleCancelSpecEdit())
    expect(result.current.specEditing).toBe(false)
  })

  it('handleCancelSpecEdit asks for confirmation when the draft changed, and respects a "no"', () => {
    global.window.confirm = vi.fn(() => false)
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    act(() => result.current.handleStartSpecEdit())
    act(() => result.current.setSpecDraft('changed'))
    act(() => result.current.handleCancelSpecEdit())

    expect(window.confirm).toHaveBeenCalled()
    expect(result.current.specEditing).toBe(true) // stayed in edit mode
  })
})

describe('useAssistCode — queueing a bridge job', () => {
  it('rejects a branch name containing whitespace without calling the API', async () => {
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    act(() => result.current.setBranchOverride('has space'))

    await act(() => result.current.handleSendToBridge())

    expect(result.current.bridgeError).toBe("Branch name can't contain whitespace")
    expect(api.queueBridgeJob).not.toHaveBeenCalled()
  })

  it("rejects a branch name starting with '-' without calling the API", async () => {
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    act(() => result.current.setBranchOverride('-flag-like'))

    await act(() => result.current.handleSendToBridge())

    expect(result.current.bridgeError).toBe("Branch name can't start with '-'")
    expect(api.queueBridgeJob).not.toHaveBeenCalled()
  })

  it('queues a job with a valid branch override', async () => {
    api.queueBridgeJob.mockResolvedValue({ id: 9, status: 'pending' })
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    act(() => result.current.setBranchOverride('my-branch'))

    await act(() => result.current.handleSendToBridge())

    expect(api.queueBridgeJob).toHaveBeenCalledWith(4, 'my-branch')
    expect(result.current.bridgeJob).toEqual({ id: 9, status: 'pending' })
  })

  it('surfaces a server error message when queueing fails', async () => {
    api.queueBridgeJob.mockRejectedValue(new Error('Card has no spec'))
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))

    await act(() => result.current.handleSendToBridge())

    expect(result.current.bridgeError).toBe('Card has no spec')
  })
})

describe('useAssistCode — resuming jobs', () => {
  it('handleResumeJob replaces bridgeJob with the resumed job', async () => {
    api.getBridgeJobChain.mockResolvedValue({ root: { id: 1, status: 'error' }, companion: null })
    api.queueResumeJob.mockResolvedValue({ id: 1, status: 'pending', resumes_job_id: 1 })
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    await waitFor(() => expect(result.current.bridgeJob?.status).toBe('error'))

    await act(() => result.current.handleResumeJob())

    expect(api.queueResumeJob).toHaveBeenCalledWith(1)
    expect(result.current.bridgeJob.status).toBe('pending')
  })

  it('handleResumeCompanionJob replaces companionJob with the resumed job', async () => {
    api.getBridgeJobChain.mockResolvedValue({
      root: { id: 1, status: 'done' },
      companion: { id: 2, status: 'stalled' },
    })
    api.queueResumeJob.mockResolvedValue({ id: 2, status: 'pending' })
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    await waitFor(() => expect(result.current.companionJob?.status).toBe('stalled'))

    await act(() => result.current.handleResumeCompanionJob())

    expect(api.queueResumeJob).toHaveBeenCalledWith(2)
    expect(result.current.companionJob.status).toBe('pending')
  })
})

describe('useAssistCode — companion job', () => {
  it('handleOpenCompanion opens the form and fetches known repos once', async () => {
    api.getKnownBridgeRepos.mockResolvedValue(['acme/one', 'acme/two'])
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))

    await act(() => { result.current.handleOpenCompanion() })
    await waitFor(() => expect(result.current.knownRepos).toEqual(['acme/one', 'acme/two']))
    expect(result.current.companionOpen).toBe(true)

    act(() => result.current.handleOpenCompanion())
    expect(api.getKnownBridgeRepos).toHaveBeenCalledTimes(1)
  })

  it('handleCancelCompanion closes the form and clears any error', () => {
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    act(() => result.current.handleOpenCompanion())
    act(() => result.current.handleCancelCompanion())
    expect(result.current.companionOpen).toBe(false)
  })

  it('handleQueueCompanion requires a repo before calling the API', async () => {
    api.getBridgeJobChain.mockResolvedValue({ root: { id: 1, status: 'done' }, companion: null })
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    await waitFor(() => expect(result.current.bridgeJob).not.toBeNull())

    await act(() => result.current.handleQueueCompanion())

    expect(result.current.companionError).toBe('Enter a repo (owner/repo)')
    expect(api.queueCompanionJob).not.toHaveBeenCalled()
  })

  it('handleQueueCompanion queues against the root job and closes the form', async () => {
    api.getBridgeJobChain.mockResolvedValue({ root: { id: 1, status: 'done' }, companion: null })
    api.queueCompanionJob.mockResolvedValue({ id: 2, status: 'pending', target_repo: 'acme/two' })
    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    await waitFor(() => expect(result.current.bridgeJob).not.toBeNull())

    act(() => result.current.setCompanionRepo('acme/two'))
    await act(() => result.current.handleQueueCompanion())

    expect(api.queueCompanionJob).toHaveBeenCalledWith(4, 'acme/two', 1)
    expect(result.current.companionJob.target_repo).toBe('acme/two')
    expect(result.current.companionOpen).toBe(false)
  })
})

describe('useAssistCode — polling', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('polls a pending bridge job until it reaches a terminal status', async () => {
    api.getBridgeJobChain.mockResolvedValue({ root: { id: 1, status: 'pending' }, companion: null })
    api.getBridgeJob
      .mockResolvedValueOnce({ id: 1, status: 'running' })
      .mockResolvedValueOnce({ id: 1, status: 'done', result: 'All set' })

    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    await vi.waitFor(() => expect(result.current.bridgeJob?.status).toBe('pending'))

    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.bridgeJob.status).toBe('running')

    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.bridgeJob.status).toBe('done')

    // Stops polling once terminal -- no further calls even after more time passes.
    const callCountAtDone = api.getBridgeJob.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
    expect(api.getBridgeJob.mock.calls.length).toBe(callCountAtDone)
  })

  it('polls a blocked/pending/running companion job until terminal', async () => {
    api.getBridgeJobChain.mockResolvedValue({
      root: { id: 1, status: 'done' },
      companion: { id: 2, status: 'blocked' },
    })
    api.getBridgeJob.mockResolvedValueOnce({ id: 2, status: 'error', result: 'nope' })

    const { result } = renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    await vi.waitFor(() => expect(result.current.companionJob?.status).toBe('blocked'))

    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.companionJob.status).toBe('error')
  })

  it('does not poll a job that is already done', async () => {
    api.getBridgeJobChain.mockResolvedValue({ root: { id: 1, status: 'done' }, companion: null })
    renderHook(() => useAssistCode(taskWithSpec, true, 'code', vi.fn()))
    await vi.waitFor(() => expect(api.getBridgeJobChain).toHaveBeenCalled())

    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(api.getBridgeJob).not.toHaveBeenCalled()
  })
})
