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
  inline = false, onSpecSaved, initialTab = 'assist',
}) {
  const [mode, setMode] = useState(initialTab)  // 'assist' | 'breakdown' | 'code'
  const [showDesc, setShowDesc] = useState(false)

  const chat = useAssistChat(task, open, initialTab, mode === 'assist', onOutputSaved)

  const handleClose = () => { chat.abortRef.current?.abort(); onClose() }

  const breakdown = useAssistBreakdown(task, open, initialTab, mode, onBreakdown, handleClose)
  const code = useAssistCode(task, open, initialTab, onSpecSaved)

  // Reset shell state (active tab, description expander) when the panel opens
  // for a task, matching the tab hooks' own reset effects (same dependencies).
  useEffect(() => {
    if (!open || !task?.id) return
    setMode(initialTab)
    setShowDesc(false)
  }, [open, task?.id, initialTab])

  // Close on Escape
  useEffect(() => {
    if (!open) return
    const onKey = e => { if (e.key === 'Escape') handleClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!task || !open) return null

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
