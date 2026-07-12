import { useState } from 'react'
import { createPortal } from 'react-dom'
import CardForm, { isoToLocal } from './CardForm'
import AssistModal from './AssistModal'
import './CardModal.css'
import './CardSheet.css'
import { SECTIONS, SECTION_LABELS } from '../../lib/sections'

const SECTION_COLORS = { today: 'var(--color-today)', week: 'var(--color-week)', month: 'var(--color-month)', later: 'var(--color-later)' }

function parseGitHubUrl(str) {
  if (!str) return null
  const m = str.match(/^https:\/\/github\.com\/([^/]+\/[^/]+)\/(pull|issues?)\/(\d+)/)
  if (!m) return null
  return { repo: m[1], type: m[2].startsWith('pull') ? 'PR' : 'Issue', number: m[3], url: str }
}

// card=null means "new card" mode (starts directly in edit mode)
export default function CardSheet({ card = null, defaultSection = 'today', allTags = [], onClose, onSave, onCreate, onDelete, onArchive, onToggle, onMove, onBreakdown }) {
  const isNew = !card?.id
  const [mode, setMode] = useState(isNew ? 'edit' : 'view')
  const [showAssist, setShowAssist] = useState(false)
  const [savedOutput, setSavedOutput] = useState(card?.thread_output ?? null)
  const [title, setTitle] = useState(card?.title ?? '')
  const [description, setDescription] = useState(card?.description ?? '')
  const [section, setSection] = useState(card?.section ?? defaultSection)
  const [scheduledAt, setScheduledAt] = useState(card?.scheduled_at ? isoToLocal(card.scheduled_at) : '')
  const [recurrenceRule, setRecurrenceRule] = useState(card?.recurrence_rule ?? '')
  const [selectedTagIds, setSelectedTagIds] = useState((card?.tags ?? []).map((t) => t.id))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const toggleTag = (id) =>
    setSelectedTagIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])

  const handleSave = async (e) => {
    e.preventDefault()
    const resolvedTitle = title.trim()
    if (!resolvedTitle) { setError('Title is required.'); return }
    setSaving(true)
    try {
      const data = {
        title: resolvedTitle,
        description: description.trim() || null,
        section,
        scheduled_at: scheduledAt || null,
        recurrence_rule: recurrenceRule || null,
        tag_ids: selectedTagIds,
      }
      if (isNew) {
        await onCreate?.(data)
      } else {
        await onSave(card.id, data)
      }
      onClose()
    } catch {
      setError('Something went wrong. Please try again.')
      setSaving(false)
    }
  }

  const handleDelete = () => {
    if (window.confirm(`Delete "${card?.title}"? This cannot be undone.`)) {
      onDelete?.(card.id)
      onClose()
    }
  }

  const gh = card ? parseGitHubUrl(card.description) : null

  return createPortal(
    <div className="card-sheet-overlay" onClick={(e) => { e.stopPropagation(); onClose() }}>
      <div className="card-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="card-sheet-handle" />

        <div className="card-sheet-header">
          {mode === 'view' ? (
            <>
              <span className="card-sheet-title">{card?.title}</span>
              <button className="card-sheet-close" onClick={onClose} aria-label="Close">&#10005;</button>
            </>
          ) : (
            <>
              <span className="card-sheet-title">{isNew ? 'New Card' : 'Edit Card'}</span>
              {!isNew && (
                <button className="card-sheet-close" onClick={() => setMode('view')} aria-label="Back">&#8592; Back</button>
              )}
              {isNew && (
                <button className="card-sheet-close" onClick={onClose} aria-label="Close">&#10005;</button>
              )}
            </>
          )}
        </div>

        {mode === 'view' ? (
          <>
            <div className="card-sheet-body">
              {card.description && (
                <div className="card-sheet-description">
                  {gh ? (
                    <a href={gh.url} target="_blank" rel="noopener noreferrer" className="card-github-badge" onClick={(e) => e.stopPropagation()}>
                      {gh.repo} #{gh.number} &#8599;
                    </a>
                  ) : (
                    <p className="card-sheet-desc-text">{card.description}</p>
                  )}
                </div>
              )}

              {savedOutput && (
                <div className="card-sheet-thread-output">
                  <div className="card-sheet-thread-output-header">
                    <span className="card-sheet-thread-output-label">✦ Assistant output</span>
                    <button className="card-sheet-btn card-sheet-btn--assist card-sheet-btn--assist-sm" onClick={() => setShowAssist(true)}>
                      Open
                    </button>
                  </div>
                  <p className="card-sheet-thread-output-text">{savedOutput}</p>
                </div>
              )}

              {(card.tags ?? []).length > 0 && (
                <div className="card-sheet-tags">
                  {(card.tags ?? []).map((tag) => (
                    <span key={tag.id} className="card-sheet-tag" style={{ background: tag.color }}>{tag.name}</span>
                  ))}
                </div>
              )}

              <div className="card-sheet-move">
                <span className="card-sheet-move-label">Move to</span>
                <div className="card-sheet-move-btns">
                  {SECTIONS.filter((s) => s !== card.section).map((s) => (
                    <button
                      key={s}
                      className="card-sheet-move-btn"
                      style={{ borderColor: SECTION_COLORS[s], color: SECTION_COLORS[s] }}
                      onClick={() => { onMove?.(card.id, s); onClose() }}
                    >
                      {SECTION_LABELS[s]}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="card-sheet-footer">
              <button
                className="card-sheet-btn card-sheet-btn--toggle"
                onClick={() => { onToggle?.(card); onClose() }}
              >
                {card.completed ? 'Mark Incomplete' : 'Mark Complete'}
              </button>
              <button className="card-sheet-btn card-sheet-btn--edit" onClick={() => setMode('edit')}>
                Edit
              </button>
              {!savedOutput && (
                <button className="card-sheet-btn card-sheet-btn--assist" onClick={() => setShowAssist(true)}>
                  ✦ Assist
                </button>
              )}
            </div>
          </>
        ) : (
          <form className="card-sheet-form" onSubmit={handleSave} noValidate>
            <div className="card-sheet-body">
              {savedOutput && (
                <div className="card-sheet-thread-output">
                  <span className="card-sheet-thread-output-label">✦ Saved output</span>
                  <p className="card-sheet-thread-output-text">{savedOutput}</p>
                </div>
              )}
              <CardForm
                idPrefix="cs"
                title={title}
                setTitle={(v) => { setTitle(v); setError('') }}
                description={description}
                setDescription={setDescription}
                section={section}
                setSection={setSection}
                scheduledAt={scheduledAt}
                setScheduledAt={setScheduledAt}
                recurrenceRule={recurrenceRule}
                setRecurrenceRule={setRecurrenceRule}
                allTags={allTags}
                selectedTagIds={selectedTagIds}
                onToggleTag={toggleTag}
                titleError={error}
                autoFocus={false}
              />
            </div>
            <div className="card-sheet-footer card-sheet-footer--edit">
              {!isNew && onDelete && (
                <button type="button" className="card-sheet-btn card-sheet-btn--danger" onClick={handleDelete}>
                  Delete
                </button>
              )}
              {!isNew && onArchive && (
                <button type="button" className="card-sheet-btn card-sheet-btn--secondary" onClick={() => { onArchive(card.id); onClose() }}>
                  Archive
                </button>
              )}
              <button type="submit" className="card-sheet-btn card-sheet-btn--save" disabled={saving}>
                {saving ? 'Saving…' : isNew ? 'Add Card' : 'Save'}
              </button>
            </div>
          </form>
        )}
      </div>

      {card && !isNew && (
        <AssistModal
          open={showAssist}
          onClose={() => setShowAssist(false)}
          task={card}
          onBreakdown={onBreakdown}
          onOutputSaved={output => setSavedOutput(output)}
        />
      )}
    </div>,
    document.body
  )
}
