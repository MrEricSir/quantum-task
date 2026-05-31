import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { SECTIONS, SECTION_LABELS } from '../App'
import Modal from './Modal'
import './AddTodoModal.css'

function isoToLocal(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

export default function AddTodoModal({ todo, allTags = [], onClose, onSave, onDelete }) {
  const isEdit = !!todo

  const [title, setTitle] = useState(todo?.title ?? '')
  const [description, setDescription] = useState(todo?.description ?? '')
  const [section, setSection] = useState(todo?.section ?? 'today')
  const [scheduledAt, setScheduledAt] = useState(
    todo?.scheduled_at ? isoToLocal(todo.scheduled_at) : ''
  )
  const [recurrenceRule, setRecurrenceRule] = useState(todo?.recurrence_rule ?? '')
  const [selectedTagIds, setSelectedTagIds] = useState(
    (todo?.tags ?? []).map((t) => t.id)
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const toggleTag = (id) => {
    setSelectedTagIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!title.trim()) { setError('Title is required.'); return }
    setSaving(true)
    try {
      await onSave({
        title: title.trim(),
        description: description.trim() || null,
        section,
        scheduled_at: scheduledAt || null,
        recurrence_rule: recurrenceRule || null,
        tag_ids: selectedTagIds,
      })
    } catch {
      setError('Something went wrong. Please try again.')
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose}>
      <Dialog.Title asChild><h2>{isEdit ? 'Edit Task' : 'New Task'}</h2></Dialog.Title>

        <form onSubmit={handleSubmit} noValidate>
          <div className="form-group">
            <label htmlFor="title">Title *</label>
            <input
              id="title"
              type="text"
              value={title}
              onChange={(e) => { setTitle(e.target.value); setError('') }}
              placeholder="What needs to be done?"
              autoFocus
            />
            {error && <span className="form-error">{error}</span>}
          </div>

          <div className="form-group">
            <label htmlFor="desc">Description</label>
            <textarea
              id="desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional details..."
              rows={3}
            />
          </div>

          <div className="form-row">
            <div className="form-group">
              <label htmlFor="section">Section</label>
              <select id="section" value={section} onChange={(e) => setSection(e.target.value)}>
                {SECTIONS.map((s) => (
                  <option key={s} value={s}>{SECTION_LABELS[s]}</option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label htmlFor="scheduled">Scheduled date &amp; time</label>
              <input
                id="scheduled"
                type="datetime-local"
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
              />
            </div>
          </div>

          <div className="form-group">
            <label htmlFor="recurrence">Repeats</label>
            <select
              id="recurrence"
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

          <div className="modal-footer">
            {isEdit && onDelete && (
              <button
                type="button"
                className="btn-danger"
                onClick={() => {
                  if (window.confirm('Delete this task? This cannot be undone.')) {
                    onDelete()
                  }
                }}
              >
                Delete
              </button>
            )}
            <button type="button" className="btn-cancel" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-save" disabled={saving}>
              {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Add Task'}
            </button>
          </div>
        </form>
    </Modal>
  )
}
