import { useState, useRef, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Cross2Icon, CopyIcon, CheckIcon } from '@radix-ui/react-icons'
import './AssistModal.css'

export default function AssistModal({ open, onClose, task }) {
  const [context,  setContext]  = useState('')
  const [output,   setOutput]   = useState('')
  const [status,   setStatus]   = useState('idle') // idle | loading | done | error
  const [copied,   setCopied]   = useState(false)
  const abortRef   = useRef(null)
  const outputRef  = useRef(null)

  // Reset when a new task is opened
  useEffect(() => {
    if (open) { setContext(''); setOutput(''); setStatus('idle'); setCopied(false) }
  }, [open, task?.id])

  // Auto-scroll output as it streams
  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [output])

  const generate = async () => {
    if (!context.trim()) return
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setOutput('')
    setStatus('loading')

    try {
      const res = await fetch('/api/assist/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_title: task.title,
          task_description: task.description || null,
          context: context.trim(),
        }),
        signal: controller.signal,
      })
      if (!res.ok) throw new Error('Server error')

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let   buffer  = ''
      let   acc     = ''

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
            if (parsed.text)  { acc += parsed.text; setOutput(acc) }
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

  const handleClose = () => { abortRef.current?.abort(); onClose() }

  if (!task) return null

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !v && handleClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="assist-overlay" />
        <Dialog.Content className="assist-modal" aria-describedby={undefined}>
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
            ) : (
              'Generate'
            )}
          </button>

          {(output || status === 'loading') && (
            <div className="assist-output-section">
              <div className="assist-output-header">
                <span className="assist-label">Output</span>
                {status === 'done' && (
                  <button className="assist-copy" onClick={copy} title="Copy to clipboard">
                    {copied ? <CheckIcon /> : <CopyIcon />}
                    {copied ? 'Copied' : 'Copy'}
                  </button>
                )}
              </div>
              <div
                ref={outputRef}
                className={`assist-output${status === 'loading' ? ' assist-output--streaming' : ''}`}
              >
                {output || <span className="assist-output-placeholder">Thinking…</span>}
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
