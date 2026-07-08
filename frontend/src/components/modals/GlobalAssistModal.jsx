import { useState, useRef, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Cross2Icon, CopyIcon, CheckIcon } from '@radix-ui/react-icons'
import { createCard } from '../../api'
import './GlobalAssistModal.css'

const SECTION_OPTIONS = [
  { value: 'today', label: "Today's cards" },
  { value: 'week',  label: "This week's cards" },
  { value: 'month', label: "This month's cards" },
  { value: 'later', label: 'Stash' },
]

const HISTORY_KEY = 'globalAssistHistory'
const HISTORY_MAX = 5

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') } catch { return [] }
}

function saveToHistory(entry) {
  try {
    const prev = loadHistory()
    const next = [entry, ...prev].slice(0, HISTORY_MAX)
    localStorage.setItem(HISTORY_KEY, JSON.stringify(next))
  } catch {}
}

function timeAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1)  return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)  return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function GlobalAssistModal({ open, onClose, tags = [] }) {
  const [contextType, setContextType] = useState('none')  // 'none' | 'section:today' | 'tag:123'
  const [prompt, setPrompt]           = useState('')
  const [output, setOutput]           = useState('')
  const [status, setStatus]           = useState('idle') // idle|loading|done|error
  const [searching, setSearching]     = useState(false)
  const [copied, setCopied]           = useState(false)
  const [savedAsCard, setSavedAsCard] = useState(false)
  const [history, setHistory]         = useState([])
  const abortRef  = useRef(null)
  const outputRef = useRef(null)
  const promptRef = useRef(null)

  useEffect(() => {
    if (open) {
      setHistory(loadHistory())
      setPrompt(''); setOutput(''); setStatus('idle')
      setSearching(false); setCopied(false); setSavedAsCard(false)
      setTimeout(() => promptRef.current?.focus(), 50)
    }
  }, [open])

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [output])

  const parseContext = () => {
    if (contextType.startsWith('section:')) return { section: contextType.slice(8), tag_id: null }
    if (contextType.startsWith('tag:'))     return { section: null, tag_id: parseInt(contextType.slice(4)) }
    return { section: null, tag_id: null }
  }

  const contextLabel = () => {
    if (contextType.startsWith('section:')) {
      const sec = contextType.slice(8)
      return SECTION_OPTIONS.find(o => o.value === sec)?.label ?? sec
    }
    if (contextType.startsWith('tag:')) {
      const id = parseInt(contextType.slice(4))
      return tags.find(t => t.id === id)?.name ?? 'tag'
    }
    return null
  }

  const generate = async () => {
    if (!prompt.trim() || status === 'loading') return
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const { section, tag_id } = parseContext()
    setOutput(''); setStatus('loading'); setSearching(false)
    setCopied(false); setSavedAsCard(false)

    let acc = ''
    try {
      const res = await fetch('/api/assist/global', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt.trim(), section, tag_id }),
        signal: controller.signal,
      })
      if (!res.ok) throw new Error('Server error')
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
          if (data === '[DONE]') { setStatus('done'); break outer }
          try {
            const parsed = JSON.parse(data)
            if (parsed.error)              { setStatus('error'); setOutput(parsed.error); return }
            if (parsed.status === 'searching') setSearching(true)
            if (parsed.text)               { setSearching(false); acc += parsed.text; setOutput(acc) }
          } catch {}
        }
      }
      setStatus('done')
      const label = contextLabel()
      saveToHistory({
        id: Date.now(),
        prompt: prompt.trim(),
        contextLabel: label,
        output: acc,
        timestamp: new Date().toISOString(),
      })
      setHistory(loadHistory())
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

  const saveAsCard = async () => {
    if (!output || savedAsCard) return
    await createCard({ title: prompt.slice(0, 80), description: output, section: 'later' })
    setSavedAsCard(true)
  }

  const loadHistoryItem = (item) => {
    setPrompt(item.prompt)
    setOutput(item.output)
    setStatus('done')
    setCopied(false)
    setSavedAsCard(false)
  }

  const handleClose = () => { abortRef.current?.abort(); onClose() }

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !v && handleClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="global-assist-overlay" />
        <Dialog.Content className="global-assist-modal" aria-describedby={undefined}>
          <div className="global-assist-header">
            <div className="global-assist-title-row">
              <span className="global-assist-spark">✦</span>
              <Dialog.Title className="global-assist-title">Assist</Dialog.Title>
            </div>
            <Dialog.Close className="global-assist-close" aria-label="Close">
              <Cross2Icon />
            </Dialog.Close>
          </div>

          <div className="global-assist-body">
            <div className="global-assist-context-row">
              <label className="global-assist-context-label" htmlFor="ga-context">Include</label>
              <select
                id="ga-context"
                className="global-assist-context-select"
                value={contextType}
                onChange={(e) => setContextType(e.target.value)}
              >
                <option value="none">No cards</option>
                <optgroup label="Board columns">
                  {SECTION_OPTIONS.map(o => (
                    <option key={o.value} value={`section:${o.value}`}>{o.label}</option>
                  ))}
                </optgroup>
                {tags.length > 0 && (
                  <optgroup label="Tags">
                    {tags.map(t => (
                      <option key={t.id} value={`tag:${t.id}`}>{t.name}</option>
                    ))}
                  </optgroup>
                )}
              </select>
            </div>
            <p className="global-assist-context-hint">Habits and health data are always included.</p>

            <textarea
              ref={promptRef}
              className="global-assist-prompt"
              placeholder="What would you like help with?"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) generate()
              }}
              rows={4}
            />

            <button
              className="global-assist-run"
              onClick={generate}
              disabled={!prompt.trim() || status === 'loading'}
            >
              {status === 'loading'
                ? <><span className="global-assist-spinner" />{searching ? 'Searching…' : 'Generating…'}</>
                : '✦ Generate'}
            </button>

            {(output || status === 'loading') && (
              <div className="global-assist-output-section">
                <div className="global-assist-output-header">
                  <span className="global-assist-output-label">Output</span>
                  {status === 'done' && (
                    <div className="global-assist-output-actions">
                      <button className="global-assist-action-btn" onClick={copy}>
                        {copied ? <><CheckIcon /> Copied</> : <><CopyIcon /> Copy</>}
                      </button>
                      <button
                        className="global-assist-action-btn"
                        onClick={saveAsCard}
                        disabled={savedAsCard}
                      >
                        {savedAsCard ? '✓ Saved' : 'Save as card'}
                      </button>
                    </div>
                  )}
                </div>
                <div
                  ref={outputRef}
                  className={`global-assist-output${status === 'loading' ? ' global-assist-output--streaming' : ''}`}
                >
                  {output || <span className="global-assist-placeholder">
                    {searching ? 'Searching the web…' : 'Thinking…'}
                  </span>}
                </div>
              </div>
            )}

            {history.length > 0 && status === 'idle' && (
              <div className="global-assist-history">
                <div className="global-assist-history-label">Recent</div>
                {history.map((item) => (
                  <button
                    key={item.id}
                    className="global-assist-history-item"
                    onClick={() => loadHistoryItem(item)}
                    title={item.output.slice(0, 200)}
                  >
                    <span className="global-assist-history-prompt">{item.prompt}</span>
                    <span className="global-assist-history-meta">
                      {item.contextLabel ? `${item.contextLabel} · ` : ''}{timeAgo(item.timestamp)}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
