import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchCardThread, sendThreadMessage, saveThreadOutput,
  updateThreadContext, clearCardThread, fetchContextFrom,
} from '../api'

// Chat/thread state for AssistModal's "Chat" tab -- extracted so AssistModal.jsx
// isn't one file owning chat, breakdown, and code/bridge state all at once.
// `isActive` is whether the Chat tab is the currently selected tab (used only
// to gate auto-focusing the input, matching AssistModal's original behavior).
export function useAssistChat(task, open, initialTab, isActive, onOutputSaved) {
  const [messages,  setMessages]  = useState([])   // [{role, content, ts}]
  const [context,   setContext]   = useState('')    // pasted reference document
  const [output,    setOutput]    = useState(null)  // saved output text
  const [input,     setInput]     = useState('')    // current chat input
  const [sending,   setSending]   = useState(false)
  const [streaming, setStreaming] = useState(false) // true while SSE is in flight
  const [threadErr, setThreadErr] = useState('')
  const [searching, setSearching] = useState(false)

  // Context panel
  const [showContext,   setShowContext]   = useState(false)
  const [editContext,   setEditContext]   = useState('')
  const [savingCtx,     setSavingCtx]     = useState(false)
  const [loadingCtxSrc, setLoadingCtxSrc] = useState(false)
  const [ctxLoadedFrom, setCtxLoadedFrom] = useState('')

  // Output save state
  const [savingOutput, setSavingOutput] = useState(null)  // index of msg being saved, or null
  const [copied,       setCopied]       = useState(null)  // index of msg copied
  const [copiedOutput, setCopiedOutput] = useState(false) // saved output copy feedback

  const abortRef     = useRef(null)
  const scrollRef    = useRef(null)
  const inputRef     = useRef(null)
  const streamingMsg = useRef('')  // accumulates the in-flight assistant message

  // ── Load thread on open ──────────────────────────────────────────────────

  useEffect(() => {
    if (!open || !task?.id) return
    setInput(''); setThreadErr(''); setSearching(false); setSending(false); setStreaming(false)
    setShowContext(false); setSavingOutput(null); setCopied(null)

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
  }, [open, task?.id, initialTab]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages])

  // Auto-focus input when chat loads and is empty
  useEffect(() => {
    if (open && isActive && !sending && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open, isActive, messages.length])

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

  const handleToggleContext = () => {
    setShowContext(v => !v); setEditContext(context); setCtxLoadedFrom('')
  }

  const handleCancelContext = () => {
    setShowContext(false); setEditContext(context); setCtxLoadedFrom('')
  }

  const saveContext = async () => {
    setSavingCtx(true)
    try {
      await updateThreadContext(task.id, editContext)
      setContext(editContext)
      setShowContext(false)
    } catch { /* ignore */ }
    setSavingCtx(false)
  }

  const loadContextFrom = async (e) => {
    const val = e.target.value
    if (!val) return
    e.target.value = ''  // reset select to placeholder
    setLoadingCtxSrc(true)
    setCtxLoadedFrom('')
    try {
      let source, section, tagId
      if (val.startsWith('section:')) {
        source = 'section'; section = val.split(':')[1]
      } else if (val.startsWith('tag:')) {
        source = 'tag'; tagId = parseInt(val.split(':')[1], 10)
      } else if (val === 'similar') {
        source = 'similar'
      }
      const data = await fetchContextFrom(task.id, source, { section, tagId })
      if (data.context_text) {
        setEditContext(data.context_text)
        setCtxLoadedFrom(`${data.count} card${data.count !== 1 ? 's' : ''} from ${data.label}`)
      } else {
        setCtxLoadedFrom(`No cards found in ${data.label}`)
      }
    } catch {
      setCtxLoadedFrom('Failed to load context')
    }
    setLoadingCtxSrc(false)
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

  const handleCopyOutput = () => {
    navigator.clipboard.writeText(output).then(() => {
      setCopiedOutput(true)
      setTimeout(() => setCopiedOutput(false), 2000)
    })
  }

  const handleClearOutput = async () => {
    if (!window.confirm('Remove saved output? This cannot be undone.')) return
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

  return {
    messages, context, output, input, setInput, sending, streaming, threadErr, searching,
    showContext, editContext, setEditContext, savingCtx, loadingCtxSrc, ctxLoadedFrom,
    savingOutput, copied, copiedOutput,
    scrollRef, inputRef, abortRef,
    send, handleKeyDown,
    handleToggleContext, handleCancelContext, saveContext, loadContextFrom,
    handleSaveOutput, handleCopyOutput, handleClearOutput, handleCopy,
    handleClearThread,
  }
}
