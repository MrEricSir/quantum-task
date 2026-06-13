import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import Modal from './Modal'
import CardForm, { ALL_SECTIONS, isoToLocal } from './CardForm'
import './AddTodoModal.css'

function formatDate(iso) {
  if (!iso) return null
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function AddTodoModal({ card, defaultSection = 'today', allTags = [], onClose, onSave, onDelete, onArchive }) {
  const isEdit = !!card?.id

  const [title, setTitle] = useState(card?.title ?? '')
  const [description, setDescription] = useState(card?.description ?? '')
  const [section, setSection] = useState(
    card?.section === 'none' ? 'later' : (card?.section ?? defaultSection)
  )
  const [scheduledAt, setScheduledAt] = useState(
    card?.scheduled_at ? isoToLocal(card.scheduled_at) : ''
  )
  const [recurrenceRule, setRecurrenceRule] = useState(card?.recurrence_rule ?? '')
  const [selectedTagIds, setSelectedTagIds] = useState(
    (card?.tags ?? []).map((t) => t.id)
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
    const resolvedTitle = title.trim() || ''
    if (!resolvedTitle) { setError('Title is required.'); return }
    setSaving(true)
    try {
      await onSave({
        title: resolvedTitle,
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
    <Modal onClose={onClose} className="modal--md">
      <Dialog.Title asChild><h2>{isEdit ? 'Edit Card' : 'New Card'}</h2></Dialog.Title>

      <form onSubmit={handleSubmit} noValidate>
        <CardForm
          idPrefix="atm"
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
        />

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
