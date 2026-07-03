import { useState, useRef, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Cross2Icon, CopyIcon, CheckIcon } from '@radix-ui/react-icons'
import { breakdownCard, commitBreakdown, createCard } from '../../api'
import './AssistModal.css'

export default function AssistModal({ open, onClose, task, onBreakdown }) {
  const [mode,     setMode]    = useState('assist')  // 'assist' | 'breakdown'

  // Assist state
  const [context,    setContext]    = useState('')
  const [output,     setOutput]     = useState('')
  const [status,     setStatus]     = useState('idle') // idle | loading | searching | done | error
  const [searching,  setSearching]  = useState(false)
  const [copied,     setCopied]     = useState(false)
  // Create-tasks flow (parsed from output)
  const [taskItems,  setTaskItems]  = useState([])
  const [taskMode,   setTaskMode]   = useState('none') // none | confirming | creating
  const abortRef   = useRef(null)
  const outputRef  = useRef(null)

  // Breakdown state
  const [bdStatus,   setBdStatus]   = useState('idle') // idle | loading | ready | saving | error
  const [bdSubtasks, setBdSubtasks] = useState([])
  const [bdTagName,  setBdTagName]  = useState('')
  const [bdError,    setBdError]    = useState('')

  // Reset all state when task or open changes
  useEffect(() => {
    if (open) {
      setMode('assist')
      setContext(''); setOutput(''); setStatus('idle'); setCopied(false); setSearching(false)
      setTaskItems([]); setTaskMode('none')
      setBdStatus('idle'); setBdSubtasks([]); setBdTagName(''); setBdError('')
    }
  }, [open, task?.id])

  // Reset breakdown state when switching to breakdown mode
  useEffect(() => {
    if (mode === 'breakdown' && bdStatus === 'idle') {
      // auto-kick off generation
      generateBreakdown()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // Auto-scroll assist output as it streams
  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [output])

  // ── Assist ────────────────────────────────────────────────────────────────

  const getLocation = () =>
    new Promise((resolve) => {
      if (!navigator.geolocation) return resolve(null)
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
        () => resolve(null),
        { timeout: 5000 },
      )
    })

  const generate = async () => {
    if (!context.trim()) return
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setOutput(''); setStatus('loading'); setSearching(false)
    const location = await getLocation()
    try {
      const res = await fetch('/api/assist/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_title: task.title,
          task_description: task.description || null,
          context: context.trim(),
          lat: location?.lat ?? null,
          lon: location?.lon ?? null,
        }),
        signal: controller.signal,
      })
      if (!res.ok) throw new Error('Server error')
      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = '', acc = ''
      outer: while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6).trim()
          if (data === '[DONE]') { setStatus('done'); break outer }
          try {
            const parsed = JSON.parse(data)
            if (parsed.error) { setStatus('error'); setOutput(parsed.error); return }
            if (parsed.status === 'searching') { setSearching(true) }
            if (parsed.text)  { setSearching(false); acc += parsed.text; setOutput(acc) }
          } catch { /* malformed chunk */ }
        }
      }
      setStatus('done')
    } catch (err) {
      if (err.name !== 'AbortError') { setStatus('error'); setOutput('Could not generate output.') }
    }
  }

  const copy = () => {
    navigator.clipboard.writeText(output).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  // ── Create tasks from output ───────────────────────────────────────────────

  const parseOutputItems = (text) => {
    const items = []
    const clean = (raw) =>
      raw
        .replace(/\*\*(.+?)\*\*/g, '$1')   // **bold**
        .replace(/\*(.+?)\*/g, '$1')         // *italic*
        .replace(/\[(.+?)\]\(.+?\)/g, '$1') // [link](url)
        .replace(/^[:#–—-]+\s*/, '')         // leading punctuation
        .trim()

    for (const line of text.split('\n')) {
      const t = line.trim()
      if (!t) continue
      let m
      // Numbered list: "1. " or "1) "
      if ((m = t.match(/^[\d]+[.)]\s+(.+)/)))      { const c = clean(m[1]); if (c.length >= 4 && c.length <= 200) items.push(c); continue }
      // Bulleted: "- " "• " "* "
      if ((m = t.match(/^[-•*]\s+(.+)/)))           { const c = clean(m[1]); if (c.length >= 4 && c.length <= 200) items.push(c); continue }
      // Markdown heading: "## Name" or "### Name"
      if ((m = t.match(/^#{1,3}\s+(.+)/)))          { const c = clean(m[1]); if (c.length >= 4 && c.length <= 200) items.push(c); continue }
      // Bold-first line used as heading: "**Name**" or "**Name** - desc"
      if ((m = t.match(/^\*\*(.+?)\*\*(.*)$/)))     { const c = clean(m[1] + m[2]); if (c.length >= 4 && c.length <= 200) items.push(c); continue }
    }
    return items
  }

  const startCreateTasks = () => {
    setTaskItems(parseOutputItems(output))
    setTaskMode('confirming')
  }

  const confirmCreateTasks = async () => {
    const valid = taskItems.filter((s) => s.trim())
    if (!valid.length) return
    setTaskMode('creating')
    try {
      const created = await Promise.all(
        valid.map((title) => createCard({ title, section: task.section ?? 'today' }))
      )
      onBreakdown?.({ cards: created })
      handleClose()
    } catch {
      setTaskMode('confirming')
    }
  }

  // ── Breakdown ─────────────────────────────────────────────────────────────

  const generateBreakdown = async () => {
    setBdStatus('loading'); setBdError('')
    try {
      const { subtasks, tag_name } = await breakdownCard(task.id)
      setBdSubtasks(subtasks)
      setBdTagName(tag_name)
      setBdStatus('ready')
    } catch {
      setBdError('Failed to generate subtasks. Please try again.')
      setBdStatus('error')
    }
  }

  const updateSubtask = (i, val) =>
    setBdSubtasks((prev) => prev.map((s, idx) => (idx === i ? val : s)))

  const removeSubtask = (i) =>
    setBdSubtasks((prev) => prev.filter((_, idx) => idx !== i))

  const confirmBreakdown = async () => {
    const valid = bdSubtasks.filter((s) => s.trim())
    if (!valid.length) return
    setBdStatus('saving')
    try {
      const result = await commitBreakdown(task.id, valid, bdTagName)
      onBreakdown?.(result)
      handleClose()
    } catch {
      setBdError('Failed to create subtasks. Please try again.')
      setBdStatus('ready')
    }
  }

  // ── Shared ────────────────────────────────────────────────────────────────

  const handleClose = () => { abortRef.current?.abort(); onClose() }

  if (!task) return null

  const validBdCount = bdSubtasks.filter((s) => s.trim()).length

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !v && handleClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="assist-overlay" />
        <Dialog.Content className="assist-modal" aria-describedby={undefined} onClick={(e) => e.stopPropagation()}>

          <div className="assist-header">
            <div className="assist-header-left">
              <span className="assist-spark">✦</span>
              <Dialog.Title className="assist-title">Assistant</Dialog.Title>
            </div>
            <Dialog.Close className="assist-close" aria-label="Close">
              <Cross2Icon />
            </Dialog.Close>
          </div>

          <div className="assist-task">
            <span className="assist-task-label">Task</span>
            <span className="assist-task-name">{task.title}</span>
          </div>

          <div className="assist-tabs">
            <button
              className={`assist-tab${mode === 'assist' ? ' assist-tab--active' : ''}`}
              onClick={() => setMode('assist')}
            >
              Assist
            </button>
            <button
              className={`assist-tab${mode === 'breakdown' ? ' assist-tab--active' : ''}`}
              onClick={() => setMode('breakdown')}
            >
              Break down
            </button>
          </div>

          {mode === 'assist' ? (
            <>
              <div className="assist-context-section">
                <label className="assist-label" htmlFor="assist-context">
                  Paste context — emails, messages, documents, notes
                </label>
                <textarea
                  id="assist-context"
                  className="assist-context"
                  placeholder="Paste anything relevant here…"
                  value={context}
                  onChange={(e) => setContext(e.target.value)}
                  rows={6}
                />
              </div>

              <button
                className="assist-run"
                onClick={generate}
                disabled={!context.trim() || status === 'loading'}
              >
                {status === 'loading' ? (
                  <><span className="assist-spinner" /> Generating…</>
                ) : 'Generate'}
              </button>

              {(output || status === 'loading') && taskMode === 'none' && (
                <div className="assist-output-section">
                  <div className="assist-output-header">
                    <span className="assist-label">Output</span>
                    {status === 'done' && (
                      <div className="assist-output-actions">
                        {parseOutputItems(output).length >= 2 && (
                          <button className="assist-copy" onClick={startCreateTasks}>
                            Create tasks
                          </button>
                        )}
                        <button className="assist-copy" onClick={copy} title="Copy to clipboard">
                          {copied ? <CheckIcon /> : <CopyIcon />}
                          {copied ? 'Copied' : 'Copy'}
                        </button>
                      </div>
                    )}
                  </div>
                  <div
                    ref={outputRef}
                    className={`assist-output${status === 'loading' ? ' assist-output--streaming' : ''}`}
                  >
                    {output || <span className="assist-output-placeholder">{searching ? 'Searching the web…' : 'Thinking…'}</span>}
                  </div>
                </div>
              )}

              {taskMode !== 'none' && (
                <>
                  <p className="assist-bd-intro">
                    Edit tasks before adding to <strong>{task.section ?? 'today'}</strong>:
                  </p>
                  <div className="assist-bd-list">
                    {taskItems.map((s, i) => (
                      <div key={i} className="assist-bd-item">
                        <span className="assist-bd-num">{i + 1}</span>
                        <input
                          className="assist-bd-input"
                          value={s}
                          onChange={(e) => setTaskItems((prev) => prev.map((x, idx) => idx === i ? e.target.value : x))}
                        />
                        <button
                          type="button"
                          className="assist-bd-remove"
                          onClick={() => setTaskItems((prev) => prev.filter((_, idx) => idx !== i))}
                          aria-label="Remove task"
                        >✕</button>
                      </div>
                    ))}
                  </div>
                  <div className="assist-task-confirm-row">
                    <button className="assist-copy" onClick={() => setTaskMode('none')}>Back</button>
                    <button
                      className="assist-run"
                      onClick={confirmCreateTasks}
                      disabled={taskMode === 'creating' || taskItems.filter(s => s.trim()).length === 0}
                    >
                      {taskMode === 'creating'
                        ? <><span className="assist-spinner" /> Creating…</>
                        : `Add ${taskItems.filter(s => s.trim()).length} task${taskItems.filter(s => s.trim()).length !== 1 ? 's' : ''}`}
                    </button>
                  </div>
                </>
              )}
            </>
          ) : (
            <>
              {bdStatus === 'loading' && (
                <div className="assist-bd-loading">
                  <span className="assist-spinner assist-spinner--dark" /> Generating subtasks…
                </div>
              )}

              {bdStatus === 'error' && !bdSubtasks.length && (
                <div className="assist-bd-error">{bdError}</div>
              )}

              {(bdStatus === 'ready' || bdStatus === 'saving') && (
                <>
                  <p className="assist-bd-intro">
                    Original card will be archived and tagged{' '}
                    <strong>{bdTagName}</strong>. Edit or remove subtasks before creating:
                  </p>
                  <div className="assist-bd-list">
                    {bdSubtasks.map((s, i) => (
                      <div key={i} className="assist-bd-item">
                        <span className="assist-bd-num">{i + 1}</span>
                        <input
                          className="assist-bd-input"
                          value={s}
                          onChange={(e) => updateSubtask(i, e.target.value)}
                        />
                        <button
                          type="button"
                          className="assist-bd-remove"
                          onClick={() => removeSubtask(i)}
                          aria-label="Remove subtask"
                        >✕</button>
                      </div>
                    ))}
                  </div>
                  {bdError && <p className="assist-bd-error">{bdError}</p>}
                  <button
                    className="assist-run"
                    onClick={confirmBreakdown}
                    disabled={bdStatus === 'saving' || validBdCount === 0}
                  >
                    {bdStatus === 'saving' ? (
                      <><span className="assist-spinner" /> Creating…</>
                    ) : `Create ${validBdCount} subtask${validBdCount !== 1 ? 's' : ''}`}
                  </button>
                </>
              )}
            </>
          )}

        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
