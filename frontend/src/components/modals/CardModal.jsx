import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import Modal from './Modal'
import CardForm, { ALL_SECTIONS, isoToLocal } from './CardForm'
import './CardModal.css'

function formatDate(iso) {
  if (!iso) return null
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function CardModal({ card, defaultSection = 'today', allTags = [], topTags = [], onClose, onSave, onDelete, onArchive, onCreateTag }) {
  const isEdit = !!card?.id

  const [title, setTitle] = useState(card?.title ?? '')
  const [description, setDescription] = useState(card?.description ?? '')
  const [section, setSection] = useState(card?.section ?? defaultSection)
  const [scheduledAt, setScheduledAt] = useState(
    card?.scheduled_at ? isoToLocal(card.scheduled_at) : ''
  )
  const [recurrenceRule, setRecurrenceRule] = useState(card?.recurrence_rule ?? '')
  const [selectedTags, setSelectedTags] = useState(card?.tags ?? [])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    const resolvedTitle = title.trim() || ''
    if (!resolvedTitle) { setError('Title is required.'); return }
    setSaving(true)
    try {
      const resolvedTags = []
      for (const tag of selectedTags) {
        if (tag.id) {
          resolvedTags.push(tag)
        } else if (onCreateTag) {
          const created = await onCreateTag({ name: tag.name, color: tag.color, is_project: false })
          if (created) resolvedTags.push(created)
        }
      }
      await onSave({
        title: resolvedTitle,
        description: description.trim() || null,
        section,
        scheduled_at: scheduledAt || null,
        recurrence_rule: recurrenceRule || null,
        tag_ids: resolvedTags.map(t => t.id),
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
          topTags={topTags}
          selectedTags={selectedTags}
          onSelectedTagsChange={setSelectedTags}
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
