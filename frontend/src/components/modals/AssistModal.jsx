import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Cross2Icon } from '@radix-ui/react-icons'
import { useAssistChat } from '../../hooks/useAssistChat'
import { useAssistBreakdown } from '../../hooks/useAssistBreakdown'
import { useAssistCode } from '../../hooks/useAssistCode'
import AssistChatTab from './AssistChatTab'
import AssistBreakdownTab from './AssistBreakdownTab'
import AssistCodeTab from './AssistCodeTab'
import descriptionToHtml from '../../lib/descriptionToHtml'
import './AssistModal.css'

// Shell only: header, task chip, tab bar, and which tab is active. All actual
// tab content/state lives in useAssistChat/useAssistBreakdown/useAssistCode +
// their matching AssistChatTab/AssistBreakdownTab/AssistCodeTab components --
// split out so this file isn't one component owning chat, breakdown, and
// code/bridge state all at once.
export default function AssistModal({
  open, onClose, task, allTags = [], onBreakdown, onOutputSaved,
  inline = false, onSpecSaved, initialTab = 'assist', githubContext = null, onTabChange,
}) {
  const [mode, setMode] = useState(initialTab)  // 'assist' | 'breakdown' | 'code'
  const [showDesc, setShowDesc] = useState(false)
  const [showSources, setShowSources] = useState(false)

  const chat = useAssistChat(task, open, initialTab, mode === 'assist', onOutputSaved)

  const handleClose = () => { chat.abortRef.current?.abort(); onClose() }

  const breakdown = useAssistBreakdown(task, open, initialTab, onBreakdown, handleClose)
  const code = useAssistCode(task, open, initialTab, onSpecSaved)

  // Reset shell state (active tab, description expander) when the panel opens
  // for a task, matching the tab hooks' own reset effects (same dependencies).
  useEffect(() => {
    if (!open || !task?.id) return
    setMode(initialTab)
    setShowDesc(false)
    setShowSources(false)
  }, [open, task?.id, initialTab])

  // Reports the active sub-tab upward (used to mirror it into the URL) -- fires on open
  // with the initial tab too, not just on later clicks, so a deep link straight into e.g.
  // Code is reflected immediately rather than only once the user clicks something.
  useEffect(() => {
    if (!open) return
    onTabChange?.(mode)
  }, [open, mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Close on Escape
  useEffect(() => {
    if (!open) return
    const onKey = e => { if (e.key === 'Escape') handleClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!task || !open) return null

  // What the chip under the task name actually says -- built once here, outside the JSX,
  // so the comment/pluralization logic isn't buried inline in the markup below.
  let sourcesLabel = null
  if (githubContext) {
    const kind = githubContext.item_type === 'pr' ? 'PR' : 'Issue'
    const commentCount = githubContext.comments?.length ?? 0
    const commentPart = commentCount > 0 ? ` (${commentCount} comment${commentCount === 1 ? '' : 's'})` : ''
    sourcesLabel = `Using ${task.description ? 'Task Notes + ' : ''}${kind} ${githubContext.repo}#${githubContext.number}${commentPart}`
  }

  const content = (
    <div
      className={inline ? 'assist-inline' : 'assist-modal'}
      role="dialog"
      aria-modal="true"
      aria-label="Assistant"
      onClick={inline ? undefined : e => e.stopPropagation()}
    >

          {/* Header — hidden in inline mode (panel provides its own header) */}
          {!inline && (
            <div className="assist-header">
              <div className="assist-header-left">
                <span className="assist-spark">✦</span>
                <span className="assist-title">Assistant</span>
              </div>
              <div className="assist-header-right">
                <button className="assist-close" aria-label="Close" onClick={handleClose}>
                  <Cross2Icon />
                </button>
              </div>
            </div>
          )}

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

          {/* Context sources -- surfaces what the assistant is actually reading. GitHub
              issue/comment context is fed into every prompt here but was otherwise
              invisible once outside the card's own view mode. */}
          {sourcesLabel && (
            <div className="assist-sources">
              <button className="assist-sources-toggle" onClick={() => setShowSources(v => !v)}>
                ⓘ {sourcesLabel}
              </button>
              {showSources && (
                <div className="assist-sources-detail">
                  If these disagree: Task Notes (written directly on this card) take priority
                  over the GitHub {githubContext.item_type === 'pr' ? 'PR' : 'issue'} description.
                  Within GitHub, a newer comment supersedes the original description or an
                  earlier comment on the same point.
                </div>
              )}
            </div>
          )}

          {/* Tabs */}
          <div className="assist-tabs">
            <button className={`assist-tab${mode === 'assist' ? ' assist-tab--active' : ''}`} onClick={() => setMode('assist')}>
              Chat
            </button>
            <button className={`assist-tab${mode === 'breakdown' ? ' assist-tab--active' : ''}`} onClick={() => setMode('breakdown')}>
              Break down
            </button>
            <button className={`assist-tab${mode === 'code' ? ' assist-tab--active' : ''}`} onClick={() => setMode('code')}>
              Code
            </button>
          </div>

          {mode === 'assist' ? (
            <AssistChatTab task={task} chat={chat} />
          ) : mode === 'breakdown' ? (
            <AssistBreakdownTab breakdown={breakdown} />
          ) : (
            <AssistCodeTab code={code} />
          )}

      </div>
  )

  if (inline) return content
  return createPortal(
    <div className="assist-overlay" onClick={handleClose} onPointerDown={e => e.stopPropagation()}>
      {content}
    </div>,
    document.body
  )
}
