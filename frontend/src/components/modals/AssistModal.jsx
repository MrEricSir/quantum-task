import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Cross2Icon, CopyIcon, CheckIcon, TrashIcon } from '@radix-ui/react-icons'
import {
  breakdownCard, commitBreakdown, bulkCreateCards,
  fetchCardThread, sendThreadMessage, saveThreadOutput,
  updateThreadContext, clearCardThread,
} from '../../api'
import descriptionToHtml from '../../lib/descriptionToHtml'
import './AssistModal.css'

export default function AssistModal({ open, onClose, task, onBreakdown, onOutputSaved }) {
  const [mode, setMode] = useState('assist')  // 'assist' | 'breakdown'

  // Thread state
  const [messages,  setMessages]  = useState([])   // [{role, content, ts}]
  const [context,   setContext]   = useState('')    // pasted reference document
  const [output,    setOutput]    = useState(null)  // saved output text
  const [input,     setInput]     = useState('')    // current chat input
  const [sending,   setSending]   = useState(false)
  const [streaming, setStreaming] = useState(false) // true while SSE is in flight
  const [threadErr, setThreadErr] = useState('')
  const [searching, setSearching] = useState(false)

  // Context panel
  const [showDesc,    setShowDesc]    = useState(false)
  const [showContext, setShowContext] = useState(false)
  const [editContext, setEditContext] = useState('')
  const [savingCtx,  setSavingCtx]   = useState(false)

  // Output save state
  const [savingOutput, setSavingOutput] = useState(null)  // index of msg being saved, or null
  const [copied,       setCopied]       = useState(null)  // index of msg copied

  // Breakdown state
  const [bdStatus,   setBdStatus]   = useState('idle')
  const [bdSubtasks, setBdSubtasks] = useState([])
  const [bdTagName,  setBdTagName]  = useState('')
  const [bdError,    setBdError]    = useState('')

  const abortRef    = useRef(null)
  const scrollRef   = useRef(null)
  const inputRef    = useRef(null)
  const streamingMsg = useRef('')  // accumulates the in-flight assistant message

  // ── Load thread on open ──────────────────────────────────────────────────

  useEffect(() => {
    if (!open || !task?.id) return
    setMode('assist')
    setInput(''); setThreadErr(''); setSearching(false); setSending(false); setStreaming(false)
    setShowDesc(false); setShowContext(false); setSavingOutput(null); setCopied(null)
    setBdStatus('idle'); setBdSubtasks([]); setBdTagName(''); setBdError('')

    fetchCardThread(task.id)
      .then(data => {
        setMessages(data.messages ?? [])
        setContext(data.context ?? '')
        setEditContext(data.context ?? '')
        setOutput(data.output ?? null)
      })
      .catch(() => {
        setMessages([]); setContext(''); setEditContext(''); setOutput(null)
      })
  }, [open, task?.id])

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages])

  // Auto-focus input when chat loads and is empty
  useEffect(() => {
    if (open && mode === 'assist' && !sending && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open, mode, messages.length])

  // Close on Escape
  useEffect(() => {
    if (!open) return
    const onKey = e => { if (e.key === 'Escape') handleClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Send a message ────────────────────────────────────────────────────────

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || sending || streaming) return

    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setSending(true); setStreaming(false); setThreadErr(''); setSearching(false)

    // Optimistically add user message to thread
    const userMsg = { role: 'user', content: text, ts: new Date().toISOString() }
    setMessages(prev => [...prev, userMsg])
    setInput('')

    // Add a placeholder assistant message that we'll fill while streaming
    streamingMsg.current = ''
    const placeholderMsg = { role: 'assistant', content: '', ts: new Date().toISOString(), _streaming: true }
    setMessages(prev => [...prev, placeholderMsg])

    try {
      const res = await sendThreadMessage(task.id, text)
      if (!res.ok) throw new Error('Server error')

      setSending(false); setStreaming(true)
      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      outer: while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6).trim()
          if (data === '[DONE]') break outer
          try {
            const parsed = JSON.parse(data)
            if (parsed.error) { setThreadErr(parsed.error); break outer }
            if (parsed.status === 'searching') { setSearching(true) }
            if (parsed.text) {
              setSearching(false)
              streamingMsg.current += parsed.text
              const acc = streamingMsg.current
              setMessages(prev => prev.map((m, i) =>
                i === prev.length - 1 ? { ...m, content: acc } : m
              ))
            }
          } catch { /* malformed chunk */ }
        }
      }

      // Mark streaming done — remove _streaming flag
      setMessages(prev => prev.map((m, i) =>
        i === prev.length - 1 ? { ...m, _streaming: false } : m
      ))
    } catch (err) {
      if (err.name !== 'AbortError') {
        setThreadErr('Could not reach the assistant.')
        // Remove placeholder
        setMessages(prev => prev.filter(m => !m._streaming))
      }
    } finally {
      setSending(false); setStreaming(false); setSearching(false)
    }
  }, [input, sending, streaming, task?.id])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  // ── Context ───────────────────────────────────────────────────────────────

  const saveContext = async () => {
    setSavingCtx(true)
    try {
      await updateThreadContext(task.id, editContext)
      setContext(editContext)
      setShowContext(false)
    } catch { /* ignore */ }
    setSavingCtx(false)
  }

  // ── Output ────────────────────────────────────────────────────────────────

  const handleSaveOutput = async (content, idx) => {
    setSavingOutput(idx)
    try {
      await saveThreadOutput(task.id, content)
      setOutput(content)
      onOutputSaved?.(content)
    } catch { /* ignore */ }
    setSavingOutput(null)
  }

  const handleClearOutput = async () => {
    try {
      await saveThreadOutput(task.id, null)
      setOutput(null)
      onOutputSaved?.(null)
    } catch { /* ignore */ }
  }

  const handleCopy = (content, idx) => {
    navigator.clipboard.writeText(content).then(() => {
      setCopied(idx)
      setTimeout(() => setCopied(null), 2000)
    })
  }

  // ── Clear thread ──────────────────────────────────────────────────────────

  const handleClearThread = async () => {
    if (!window.confirm('Clear this conversation? This cannot be undone.')) return
    await clearCardThread(task.id)
    setMessages([]); setOutput(null); setContext(''); setEditContext('')
    onOutputSaved?.(null)
  }

  // ── Breakdown ─────────────────────────────────────────────────────────────

  useEffect(() => {
    if (mode === 'breakdown' && bdStatus === 'idle') generateBreakdown()
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  const generateBreakdown = async () => {
    setBdStatus('loading'); setBdError('')
    try {
      const { subtasks, tag_name } = await breakdownCard(task.id)
      setBdSubtasks(subtasks); setBdTagName(tag_name); setBdStatus('ready')
    } catch {
      setBdError('Failed to generate subtasks.'); setBdStatus('error')
    }
  }

  const confirmBreakdown = async () => {
    const valid = bdSubtasks.filter(s => s.trim())
    if (!valid.length) return
    setBdStatus('saving')
    try {
      const result = await commitBreakdown(task.id, valid, bdTagName)
      onBreakdown?.(result); handleClose()
    } catch {
      setBdError('Failed to create subtasks.'); setBdStatus('ready')
    }
  }

  // ── Shared ────────────────────────────────────────────────────────────────

  const handleClose = () => { abortRef.current?.abort(); onClose() }

  if (!task || !open) return null

  const validBdCount = bdSubtasks.filter(s => s.trim()).length
  const hasHistory   = messages.length > 0

  return createPortal(
    <div className="assist-overlay" onClick={handleClose} onPointerDown={e => e.stopPropagation()}>
      <div
        className="assist-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Assistant"
        onClick={e => e.stopPropagation()}
      >

          {/* Header */}
          <div className="assist-header">
            <div className="assist-header-left">
              <span className="assist-spark">✦</span>
              <span className="assist-title">Assistant</span>
            </div>
            <div className="assist-header-right">
              {hasHistory && mode === 'assist' && (
                <button className="assist-icon-btn" onClick={handleClearThread} title="Clear conversation">
                  <TrashIcon />
                </button>
              )}
              <button className="assist-close" aria-label="Close" onClick={handleClose}>
                <Cross2Icon />
              </button>
            </div>
          </div>

          {/* Task chip */}
          <div className="assist-task">
            <span className="assist-task-label">Task</span>
            <div className="assist-task-body">
              <div className="assist-task-title-row">
                <span className="assist-task-name">{task.title}</span>
                {task.description && (
                  <button className="assist-task-desc-toggle" onClick={() => setShowDesc(v => !v)}>
                    {showDesc ? 'Hide' : 'Details'}
                  </button>
                )}
              </div>
              {task.description && showDesc && (
                <span className="assist-task-desc" dangerouslySetInnerHTML={{ __html: descriptionToHtml(task.description) }} />
              )}
            </div>
          </div>

          {/* Tabs */}
          <div className="assist-tabs">
            <button className={`assist-tab${mode === 'assist' ? ' assist-tab--active' : ''}`} onClick={() => setMode('assist')}>
              Chat
            </button>
            <button className={`assist-tab${mode === 'breakdown' ? ' assist-tab--active' : ''}`} onClick={() => setMode('breakdown')}>
              Break down
            </button>
          </div>

          {mode === 'assist' ? (
            <>
              {/* Saved output panel */}
              {output && (
                <div className="assist-output-panel">
                  <div className="assist-output-panel-header">
                    <span className="assist-label">Saved output</span>
                    <button className="assist-icon-btn" onClick={handleClearOutput} title="Remove saved output">
                      <TrashIcon />
                    </button>
                  </div>
                  <div className="assist-output-panel-text">{output}</div>
                </div>
              )}

              {/* Context panel (collapsible) */}
              <div className="assist-context-panel">
                <button
                  className="assist-context-toggle"
                  onClick={() => { setShowContext(v => !v); setEditContext(context) }}
                >
                  <span>Reference document</span>
                  <span className={`assist-context-caret${showContext ? ' assist-context-caret--open' : ''}`}>▾</span>
                  {context && <span className="assist-context-dot" />}
                </button>
                {showContext && (
                  <div className="assist-context-body">
                    <textarea
                      className="assist-context-input"
                      placeholder="Paste an email, document, or any reference text here. The assistant will use it throughout the conversation."
                      value={editContext}
                      onChange={e => setEditContext(e.target.value)}
                      rows={5}
                    />
                    <div className="assist-context-actions">
                      <button className="assist-copy" onClick={() => { setShowContext(false); setEditContext(context) }}>Cancel</button>
                      <button className="assist-run assist-run--sm" onClick={saveContext} disabled={savingCtx}>
                        {savingCtx ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* Message thread */}
              <div className="assist-thread" ref={scrollRef}>
                {!hasHistory && (
                  <div className="assist-thread-empty">
                    Start a conversation about this task — the assistant will remember it.
                  </div>
                )}
                {messages.map((msg, idx) => (
                  <div key={idx} className={`assist-msg assist-msg--${msg.role}`}>
                    <div className="assist-msg-bubble">
                      {msg._streaming && !msg.content
                        ? <span className="assist-msg-placeholder">{searching ? 'Searching the web…' : 'Thinking…'}</span>
                        : <span className="assist-msg-text">{msg.content}</span>
                      }
                    </div>
                    {msg.role === 'assistant' && !msg._streaming && msg.content && (
                      <div className="assist-msg-actions">
                        <button
                          className="assist-msg-action"
                          onClick={() => handleCopy(msg.content, idx)}
                          title="Copy"
                        >
                          {copied === idx ? <CheckIcon /> : <CopyIcon />}
                        </button>
                        <button
                          className="assist-msg-action"
                          onClick={() => handleSaveOutput(msg.content, idx)}
                          disabled={savingOutput === idx}
                          title="Save as output"
                        >
                          {savingOutput === idx ? '…' : output === msg.content ? '✓ Saved' : 'Save'}
                        </button>
                      </div>
                    )}
                  </div>
                ))}
                {threadErr && <div className="assist-thread-error">{threadErr}</div>}
              </div>

              {/* Input */}
              <div className="assist-input-row">
                <textarea
                  ref={inputRef}
                  className="assist-input"
                  placeholder="Message…"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  rows={2}
                  disabled={sending || streaming}
                />
                <button
                  className="assist-send"
                  onClick={send}
                  disabled={!input.trim() || sending || streaming}
                >
                  {sending || streaming ? <span className="assist-spinner" /> : '↑'}
                </button>
              </div>
            </>
          ) : (
            <>
              {bdStatus === 'loading' && (
                <div className="assist-bd-loading">
                  <span className="assist-spinner assist-spinner--dark" /> Generating subtasks…
                </div>
              )}
              {bdStatus === 'error' && <div className="assist-bd-error">{bdError}</div>}
              {(bdStatus === 'ready' || bdStatus === 'saving') && (
                <>
                  <p className="assist-bd-intro">
                    Original card will be archived and tagged <strong>{bdTagName}</strong>. Edit or remove subtasks before creating:
                  </p>
                  <div className="assist-bd-list">
                    {bdSubtasks.map((s, i) => (
                      <div key={i} className="assist-bd-item">
                        <span className="assist-bd-num">{i + 1}</span>
                        <input
                          className="assist-bd-input"
                          value={s}
                          onChange={e => setBdSubtasks(prev => prev.map((x, idx) => idx === i ? e.target.value : x))}
                        />
                        <button type="button" className="assist-bd-remove"
                          onClick={() => setBdSubtasks(prev => prev.filter((_, idx) => idx !== i))}
                          aria-label="Remove"
                        >✕</button>
                      </div>
                    ))}
                  </div>
                  {bdError && <p className="assist-bd-error">{bdError}</p>}
                  <button className="assist-run" onClick={confirmBreakdown}
                    disabled={bdStatus === 'saving' || validBdCount === 0}
                  >
                    {bdStatus === 'saving'
                      ? <><span className="assist-spinner" /> Creating…</>
                      : `Create ${validBdCount} subtask${validBdCount !== 1 ? 's' : ''}`}
                  </button>
                </>
              )}
            </>
          )}

      </div>
    </div>,
    document.body
  )
}
