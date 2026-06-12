import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { parseBulkTodos } from '../api'
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

// All valid sections: board columns + "none" (reference card, shown on Cards page only)
const ALL_SECTIONS = ['none', 'today', 'week', 'month', 'later']
const ALL_SECTION_LABELS = {
  none: 'Reference (no section)',
  today: 'Today',
  week: 'This Week',
  month: 'This Month',
  later: 'Later',
}

const TYPE_LABELS = { task: 'Task', habit: 'Habit' }

export default function QuickAddModal({ allTags = [], onClose, onSaveTask, onSaveHabit }) {
  // ── Input step ──
  const [text, setText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState('')

  // ── Confirm / bulk-edit step ──
  const [step, setStep] = useState('input') // 'input' | 'confirm' | 'bulk-confirm' | 'bulk-edit'
  const [detectedType, setDetectedType] = useState('task')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [section, setSection] = useState('later')
  const [scheduledAt, setScheduledAt] = useState('')
  const [selectedTagIds, setSelectedTagIds] = useState([])
  const [clarificationQuestion, setClarificationQuestion] = useState('')
  const [saving, setSaving] = useState(false)

  // ── Bulk confirm step ──
  const [bulkItems, setBulkItems] = useState([])
  const [editingBulkIdx, setEditingBulkIdx] = useState(null)

  const toggleTag = (id) =>
    setSelectedTagIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])

  const handleParse = async () => {
    if (!text.trim()) return
    setParsing(true)
    setParseError('')
    try {
      const { items } = await parseBulkTodos(text.trim())
      if (items.length === 1) {
        const result = items[0]
        const tagIds = (result.suggested_tags ?? [])
          .map((name) => allTags.find((t) => t.name.toLowerCase() === name.toLowerCase())?.id)
          .filter(Boolean)
        setDetectedType(result.type ?? 'task')
        setTitle(result.title ?? '')
        setDescription(result.description ?? '')
        setSection(result.section ?? 'later')
        setScheduledAt(result.scheduled_at ? isoToLocal(result.scheduled_at) : '')
        setSelectedTagIds(tagIds)
        setClarificationQuestion(result.clarification_question ?? '')
        setStep('confirm')
      } else {
        setBulkItems(items.map((item, i) => ({ ...item, _key: i })))
        setStep('bulk-confirm')
      }
    } catch (e) {
      setParseError(e.message || 'Failed to parse. Check your connection.')
    } finally {
      setParsing(false)
    }
  }

  const buildCardPayload = () => ({
    title: title.trim(),
    description: description.trim() || null,
    section,
    // scheduled_at only applies to board cards, not reference cards
    scheduled_at: section !== 'none' ? (scheduledAt || null) : null,
    tag_ids: selectedTagIds,
    // raw_input preserved verbatim for debugging LLM parse results
    raw_input: text,
  })

  const handleConfirm = async () => {
    setSaving(true)
    try {
      if (detectedType === 'habit') {
        await onSaveHabit({ name: title, tag_ids: selectedTagIds })
      } else {
        await onSaveTask(buildCardPayload())
      }
      onClose()
    } catch {
      // keep modal open on error
    } finally {
      setSaving(false)
    }
  }

  const enterBulkEdit = (idx) => {
    const item = bulkItems[idx]
    setEditingBulkIdx(idx)
    setDetectedType(item.type ?? 'task')
    setTitle(item.title ?? '')
    setDescription(item.description ?? '')
    setSection(item.section ?? 'later')
    setScheduledAt(item.scheduled_at ? isoToLocal(item.scheduled_at) : '')
    setSelectedTagIds(item._tag_ids ?? (item.suggested_tags ?? [])
      .map((name) => allTags.find((t) => t.name.toLowerCase() === name.toLowerCase())?.id)
      .filter(Boolean))
    setClarificationQuestion('')
    setStep('bulk-edit')
  }

  const saveBulkEdit = () => {
    setBulkItems((prev) => prev.map((it, i) => i === editingBulkIdx ? {
      ...it,
      type: detectedType,
      title,
      description,
      section,
      scheduled_at: scheduledAt || null,
      _tag_ids: selectedTagIds,
    } : it))
    setStep('bulk-confirm')
  }

  const handleBulkConfirm = async () => {
    setSaving(true)
    for (const item of bulkItems) {
      if (item._removed) continue
      const tagIds = item._tag_ids ?? (item.suggested_tags ?? [])
        .map((name) => allTags.find((t) => t.name.toLowerCase() === name.toLowerCase())?.id)
        .filter(Boolean)
      try {
        if (item.type === 'habit') {
          await onSaveHabit({ name: item.title, tag_ids: tagIds })
        } else {
          await onSaveTask({
            title: item.title,
            description: item.description || null,
            section: item.section ?? 'later',
            scheduled_at: item.section !== 'none' ? (item.scheduled_at || null) : null,
            tag_ids: tagIds,
            raw_input: text,
          })
        }
      } catch {
        // continue saving remaining items even if one fails
      }
    }
    setSaving(false)
    onClose()
  }

  const confirmDisabled = saving || !title.trim()

  // Shared fields rendered in both the confirm and bulk-edit screens
  const renderCardFields = (idPrefix) => (
    <>
      <div className="form-group">
        <label htmlFor={`${idPrefix}-title`}>Title</label>
        <input
          id={`${idPrefix}-title`}
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          autoFocus={detectedType === 'task'}
        />
      </div>
      <div className="form-group">
        <label htmlFor={`${idPrefix}-desc`}>Description</label>
        <textarea
          id={`${idPrefix}-desc`}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          placeholder="Optional"
        />
      </div>
      <div className="form-row">
        <div className="form-group">
          <label htmlFor={`${idPrefix}-section`}>Section</label>
          <select id={`${idPrefix}-section`} value={section} onChange={(e) => setSection(e.target.value)}>
            {ALL_SECTIONS.map((s) => (
              <option key={s} value={s}>{ALL_SECTION_LABELS[s]}</option>
            ))}
          </select>
        </div>
        {section !== 'none' && (
          <div className="form-group">
            <label htmlFor={`${idPrefix}-scheduled`}>Scheduled</label>
            <input
              id={`${idPrefix}-scheduled`}
              type="datetime-local"
              value={scheduledAt}
              onChange={(e) => setScheduledAt(e.target.value)}
            />
          </div>
        )}
      </div>
    </>
  )

  return (
    <Modal onClose={onClose} className="modal--md quick-modal">

      {step === 'input' && (
        <>
          <Dialog.Title asChild><h2>Quick Add</h2></Dialog.Title>
          <p className="quick-hint">
            Describe a task, habit, or reference item in plain language. Put multiple items on separate lines to add them all at once.
          </p>
          <textarea
            className="quick-textarea"
            placeholder={"dentist appointment tomorrow at 10am\nmeditate every morning\nshopping list: milk, eggs, bread"}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleParse() }}
            autoFocus
            rows={4}
          />
          {parseError && (
            <p className="quick-parse-error">{parseError}</p>
          )}
          <div className="modal-footer">
            <button className="btn-cancel" onClick={onClose}>Cancel</button>
            <button className="btn-save" onClick={handleParse} disabled={!text.trim() || parsing}>
              {parsing ? 'Thinking…' : 'Add'}
            </button>
          </div>
        </>
      )}

      {step === 'bulk-confirm' && (
        <>
          <Dialog.Title asChild><h2>Add {bulkItems.filter((i) => !i._removed).length} Items</h2></Dialog.Title>
          <div className="quick-bulk-list">
            {bulkItems.map((item, idx) => (
              !item._removed && (
                <div key={item._key} className="quick-bulk-item" onClick={() => enterBulkEdit(idx)} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && enterBulkEdit(idx)}>
                  <span className={`quick-bulk-type-badge quick-bulk-type-badge--${item.type}`}>
                    {TYPE_LABELS[item.type] ?? 'Task'}
                  </span>
                  <span className="quick-bulk-title">{item.title}</span>
                  <button
                    type="button"
                    className="quick-bulk-remove"
                    onClick={(e) => { e.stopPropagation(); setBulkItems((prev) =>
                      prev.map((it, i) => i === idx ? { ...it, _removed: true } : it)
                    )}}
                    aria-label="Remove"
                  >✕</button>
                </div>
              )
            ))}
          </div>
          <div className="modal-footer">
            <button className="btn-cancel" onClick={() => setStep('input')}>Back</button>
            <button
              className="btn-save"
              onClick={handleBulkConfirm}
              disabled={saving || bulkItems.every((i) => i._removed)}
            >
              {saving ? 'Saving…' : 'Add All'}
            </button>
          </div>
        </>
      )}

      {step === 'bulk-edit' && (
        <>
          <Dialog.Title asChild><h2>Edit Item</h2></Dialog.Title>

          <div className="quick-type-row">
            <span className="quick-type-label">Type</span>
            <div className="quick-type-tabs">
              {['task', 'habit'].map((t) => (
                <button key={t} type="button"
                  className={`quick-type-tab${detectedType === t ? ' quick-type-tab--active' : ''}`}
                  onClick={() => setDetectedType(t)}
                >{TYPE_LABELS[t]}</button>
              ))}
            </div>
          </div>

          {detectedType === 'task' && renderCardFields('qe')}

          {detectedType === 'habit' && (
            <div className="form-group">
              <label htmlFor="qe-habit">Habit name</label>
              <input id="qe-habit" type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
            </div>
          )}

          {allTags.length > 0 && (
            <div className="form-group">
              <label>Tags</label>
              <div className="tag-toggles">
                {allTags.map((tag) => {
                  const on = selectedTagIds.includes(tag.id)
                  return (
                    <button key={tag.id} type="button"
                      className={`tag-toggle ${on ? 'tag-toggle--on' : ''}`}
                      style={on ? { background: tag.color, borderColor: tag.color, color: '#fff' } : { borderColor: tag.color, color: tag.color }}
                      onClick={() => toggleTag(tag.id)}
                    >{tag.name}</button>
                  )
                })}
              </div>
            </div>
          )}

          <div className="modal-footer">
            <button className="btn-cancel" onClick={() => setStep('bulk-confirm')}>Back</button>
            <button className="btn-save" onClick={saveBulkEdit} disabled={!title.trim()}>
              Save
            </button>
          </div>
        </>
      )}

      {step === 'confirm' && (
        <>
          <Dialog.Title asChild><h2>Confirm</h2></Dialog.Title>

          {clarificationQuestion && (
            <div className="quick-clarification">
              <span className="quick-clarification-icon">💬</span>
              {clarificationQuestion}
            </div>
          )}

          <div className="quick-type-row">
            <span className="quick-type-label">Detected as</span>
            <div className="quick-type-tabs">
              {['task', 'habit'].map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`quick-type-tab${detectedType === t ? ' quick-type-tab--active' : ''}`}
                  onClick={() => setDetectedType(t)}
                >
                  {TYPE_LABELS[t]}
                </button>
              ))}
            </div>
          </div>

          {detectedType === 'task' && renderCardFields('qa')}

          {detectedType === 'habit' && (
            <div className="form-group">
              <label htmlFor="qa-habit">Habit name</label>
              <input
                id="qa-habit"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                autoFocus
              />
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

          <div className="modal-footer">
            <button className="btn-cancel" onClick={() => setStep('input')}>Back</button>
            <button className="btn-save" onClick={handleConfirm} disabled={confirmDisabled}>
              {saving ? 'Saving…' : `Add ${TYPE_LABELS[detectedType]}`}
            </button>
          </div>
        </>
      )}

    </Modal>
  )
}
