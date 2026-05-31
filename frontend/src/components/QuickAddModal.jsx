import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { SECTIONS, SECTION_LABELS } from '../App'
import Modal from './Modal'
import './QuickAddModal.css'

function isoToLocal(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

// Input mode:   pass onSubmit(text) — submits text to queue and closes
// Confirm mode: pass preloaded={ text, result } + onSave(data) — shows editable preview
export default function QuickAddModal({ allTags = [], onClose, onSubmit, onSave, preloaded = null }) {
  const isConfirm = !!preloaded

  // Input mode state
  const [text, setText] = useState('')

  // Confirm mode state (initialized from preloaded)
  const [title, setTitle] = useState(preloaded?.result?.title ?? '')
  const [description, setDescription] = useState(preloaded?.result?.description ?? '')
  const [section, setSection] = useState(preloaded?.result?.section ?? 'later')
  const [scheduledAt, setScheduledAt] = useState(
    preloaded?.result?.scheduled_at ? isoToLocal(preloaded.result.scheduled_at) : ''
  )
  const [selectedTagIds, setSelectedTagIds] = useState(() => {
    if (!preloaded) return []
    return (preloaded.result?.suggested_tags ?? [])
      .map((name) => allTags.find((t) => t.name === name)?.id)
      .filter(Boolean)
  })
  const [saving, setSaving] = useState(false)

  const toggleTag = (id) => {
    setSelectedTagIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }

  const handleSubmit = () => {
    if (!text.trim()) return
    onSubmit(text.trim())
    onClose()
  }

  const handleSave = async () => {
    if (!title.trim()) return
    setSaving(true)
    await onSave({
      title: title.trim(),
      description: description.trim() || null,
      section,
      scheduled_at: scheduledAt || null,
      tag_ids: selectedTagIds,
      raw_input: preloaded.text,
    })
  }

  return (
    <Modal onClose={onClose} className="quick-modal">

        {/* ── Input mode ── */}
        {!isConfirm && (
          <>
            <Dialog.Title asChild><h2>Quick Add</h2></Dialog.Title>
            <p className="quick-hint">Describe a task or habit in plain language — it will be parsed and added automatically.</p>
            <textarea
              className="quick-textarea"
              placeholder={'e.g. "dentist appointment tomorrow at 10am"\n     "send Bob the report by Friday"'}
              value={text}
              onChange={(e) => setText(e.target.value)}
              autoFocus
              rows={4}
            />
            <div className="modal-footer">
              <button className="btn-cancel" onClick={onClose}>Cancel</button>
              <button className="btn-save" onClick={handleSubmit} disabled={!text.trim()}>
                Add
              </button>
            </div>
          </>
        )}

        {/* ── Confirm mode ── */}
        {isConfirm && (
          <>
            <Dialog.Title asChild><h2>Confirm Task</h2></Dialog.Title>
            <p className="quick-hint">Edit any field before adding.</p>

            {preloaded.text && (
              <div className="form-group">
                <label>Original note</label>
                <div className="raw-input-display">{preloaded.text}</div>
              </div>
            )}

            <div className="form-group">
              <label htmlFor="qa-title">Title</label>
              <input id="qa-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
            </div>

            <div className="form-group">
              <label htmlFor="qa-desc">Description</label>
              <textarea id="qa-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} placeholder="Optional" />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="qa-section">Section</label>
                <select id="qa-section" value={section} onChange={(e) => setSection(e.target.value)}>
                  {SECTIONS.map((s) => (
                    <option key={s} value={s}>{SECTION_LABELS[s]}</option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="qa-scheduled">Scheduled</label>
                <input
                  id="qa-scheduled"
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(e) => setScheduledAt(e.target.value)}
                />
              </div>
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
              <button className="btn-cancel" onClick={onClose}>Cancel</button>
              <button className="btn-save" onClick={handleSave} disabled={!title.trim() || saving}>
                {saving ? 'Adding…' : 'Add Task'}
              </button>
            </div>
          </>
        )}

    </Modal>
  )
}
