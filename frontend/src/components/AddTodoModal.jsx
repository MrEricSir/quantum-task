import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import Modal from './Modal'
import './AddTodoModal.css'

// Section options for all card types. "none" = reference card (Cards page only).
const ALL_SECTIONS = [
  { value: 'today', label: 'Today' },
  { value: 'week',  label: 'This Week' },
  { value: 'month', label: 'This Month' },
  { value: 'later', label: 'Later' },
  { value: 'none',  label: 'Reference (no section)' },
]

function formatDate(iso) {
  if (!iso) return null
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function isoToLocal(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

export default function AddTodoModal({ card, defaultSection = 'today', allTags = [], onClose, onSave, onDelete, onArchive }) {
  const isEdit = !!card?.id

  const [title, setTitle] = useState(card?.title ?? '')
  const [description, setDescription] = useState(card?.description ?? '')
  const [section, setSection] = useState(card?.section ?? defaultSection)
  const [scheduledAt, setScheduledAt] = useState(
    card?.scheduled_at ? isoToLocal(card.scheduled_at) : ''
  )
  const [recurrenceRule, setRecurrenceRule] = useState(card?.recurrence_rule ?? '')
  const [selectedTagIds, setSelectedTagIds] = useState(
    (card?.tags ?? []).map((t) => t.id)
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const isReference = section === 'none'

  const toggleTag = (id) => {
    setSelectedTagIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    // Title is optional for reference cards — derive from first line of description if blank
    const resolvedTitle = title.trim()
      || description.split('\n')[0].trim().slice(0, 120)
      || ''
    if (!resolvedTitle) { setError('Title or description is required.'); return }
    setSaving(true)
    try {
      await onSave({
        title: resolvedTitle,
        description: description.trim() || null,
        section,
        // Scheduled date and recurrence only apply to board cards
        scheduled_at: isReference ? null : (scheduledAt || null),
        recurrence_rule: isReference ? null : (recurrenceRule || null),
        tag_ids: selectedTagIds,
      })
    } catch {
      setError('Something went wrong. Please try again.')
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} className="modal--md">
      <Dialog.Title asChild><h2>{isEdit ? 'Edit Card' : 'New Card'}</h2></Dialog.Title>

      <form onSubmit={handleSubmit} noValidate>
        <div className="form-group">
          <label htmlFor="atm-title">Title</label>
          <input
            id="atm-title"
            type="text"
            value={title}
            onChange={(e) => { setTitle(e.target.value); setError('') }}
            placeholder={isReference ? 'Optional' : 'What needs to be done?'}
            autoFocus={!isReference}
          />
          {error && <span className="form-error">{error}</span>}
        </div>

        <div className="form-group">
          <label htmlFor="atm-desc">Description</label>
          <textarea
            id="atm-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={isReference ? 'Card content…' : 'Optional details…'}
            rows={isReference ? 8 : 3}
            autoFocus={isReference}
          />
        </div>

        <div className="form-row">
          <div className="form-group">
            <label htmlFor="atm-section">Section</label>
            <select id="atm-section" value={section} onChange={(e) => setSection(e.target.value)}>
              {ALL_SECTIONS.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>

          {!isReference && (
            <div className="form-group">
              <label htmlFor="atm-scheduled">Scheduled date &amp; time</label>
              <input
                id="atm-scheduled"
                type="datetime-local"
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
              />
            </div>
          )}
        </div>

        {!isReference && (
          <div className="form-group">
            <label htmlFor="atm-recurrence">Repeats</label>
            <select
              id="atm-recurrence"
              value={recurrenceRule}
              onChange={(e) => setRecurrenceRule(e.target.value)}
            >
              <option value="">Does not repeat</option>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
              <option value="yearly">Yearly</option>
            </select>
          </div>
        )}

        {allTags.length > 0 && (
          <div className="form-group">
            <label>Tags</label>
            <div className="tag-toggles">
              {allTags.map((tag) => {
                const on = selectedTagIds.includes(tag.id)
                return (
                  <button
                    key={tag.id}
                    type="button"
                    className={`tag-toggle ${on ? 'tag-toggle--on' : ''}`}
                    style={on
                      ? { background: tag.color, borderColor: tag.color, color: '#fff' }
                      : { borderColor: tag.color, color: tag.color }
                    }
                    onClick={() => toggleTag(tag.id)}
                  >
                    {tag.name}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {isEdit && card?.created_at && (
          <div className="form-hint">Added {formatDate(card.created_at)}</div>
        )}

        <div className="modal-footer">
          {isEdit && onDelete && (
            <button
              type="button"
              className="btn-danger"
              onClick={() => {
                if (window.confirm('Delete this card? This cannot be undone.')) {
                  onDelete()
                }
              }}
            >
              Delete
            </button>
          )}
          {isEdit && onArchive && (
            <button
              type="button"
              className="btn-secondary"
              onClick={onArchive}
            >
              Archive
            </button>
          )}
          <button type="button" className="btn-cancel" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-save" disabled={saving}>
            {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Add Card'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
