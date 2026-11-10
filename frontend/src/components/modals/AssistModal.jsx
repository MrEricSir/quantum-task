import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Cross2Icon } from '@radix-ui/react-icons'
import { useAssistChat } from '../../hooks/useAssistChat'
import { useAssistBreakdown } from '../../hooks/useAssistBreakdown'
import AssistChatTab from './AssistChatTab'
import AssistBreakdownTab from './AssistBreakdownTab'
import AssistTaskHeader from './AssistTaskHeader'
import './AssistModal.css'

// Shell only: header, task chip, tab bar, and which tab is active. All actual
// tab content/state lives in useAssistChat/useAssistBreakdown + their matching
// AssistChatTab/AssistBreakdownTab components -- split out so this file isn't
// one component owning both tabs' state at once. Code used to be a third tab
// here too; it's now CardDetailPanel's own top-level mode, a peer of View/Edit/
// Assist rather than nested inside this one (see useAssistCode.js/AssistCodeTab.jsx,
// rendered directly by CardDetailPanel).
export default function AssistModal({
  open, onClose, task, allTags = [], onBreakdown, onOutputSaved,
  inline = false, initialTab = 'assist', githubContext = null, onTabChange,
}) {
  const [mode, setMode] = useState(initialTab)  // 'assist' | 'breakdown'

  const chat = useAssistChat(task, open, initialTab, mode === 'assist', onOutputSaved)

  const handleClose = () => { chat.abortRef.current?.abort(); onClose() }

  const breakdown = useAssistBreakdown(task, open, initialTab, onBreakdown, handleClose)

  // Reset shell state (active tab) when the panel opens for a task, matching
  // the tab hooks' own reset effects (same dependencies).
  useEffect(() => {
    if (!open || !task?.id) return
    setMode(initialTab)
  }, [open, task?.id, initialTab])

  // Reports the active sub-tab upward (used to mirror it into the URL) -- fires on open
  // with the initial tab too, not just on later clicks, so a deep link straight into e.g.
  // Break down is reflected immediately rather than only once the user clicks something.
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

          <AssistTaskHeader task={task} githubContext={githubContext} />

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
            <AssistChatTab task={task} chat={chat} />
          ) : (
            <AssistBreakdownTab breakdown={breakdown} />
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
