import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import Modal from './Modal'
import { breakdownCard, commitBreakdown } from '../../api'
import './BreakdownModal.css'

export default function BreakdownModal({ card, onClose, onCommit }) {
  const [subtasks, setSubtasks] = useState(null)
  const [tagName, setTagName] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    breakdownCard(card.id)
      .then(({ subtasks, tag_name }) => {
        setSubtasks(subtasks)
        setTagName(tag_name)
        setLoading(false)
      })
      .catch(() => {
        setError('Failed to generate subtasks. Please try again.')
        setLoading(false)
      })
  }, [card.id])

  const updateSubtask = (i, val) =>
    setSubtasks((prev) => prev.map((s, idx) => (idx === i ? val : s)))

  const removeSubtask = (i) =>
    setSubtasks((prev) => prev.filter((_, idx) => idx !== i))

  const handleConfirm = async () => {
    const valid = subtasks.filter((s) => s.trim())
    if (!valid.length) return
    setSaving(true)
    try {
      const result = await commitBreakdown(card.id, valid)
      onCommit(result)
    } catch {
      setError('Failed to create subtasks. Please try again.')
      setSaving(false)
    }
  }

  const validCount = subtasks ? subtasks.filter((s) => s.trim()).length : 0

  return (
    <Modal onClose={onClose} className="modal--md">
      <Dialog.Title asChild>
        <h2>Break down task</h2>
      </Dialog.Title>

      <p className="breakdown-card-title">{card.title}</p>

      {loading ? (
        <div className="breakdown-loading">Generating subtasks…</div>
      ) : error && !subtasks ? (
        <div className="breakdown-error">{error}</div>
      ) : (
        <>
          <p className="breakdown-intro">
            The original card will be archived and tagged{' '}
            <strong>{tagName}</strong>. Edit or remove subtasks before creating:
          </p>
          <div className="breakdown-list">
            {subtasks.map((s, i) => (
              <div key={i} className="breakdown-item">
                <span className="breakdown-num">{i + 1}</span>
                <input
                  className="breakdown-input"
                  value={s}
                  onChange={(e) => updateSubtask(i, e.target.value)}
                />
                <button
                  type="button"
                  className="breakdown-remove"
                  onClick={() => removeSubtask(i)}
                  aria-label="Remove subtask"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
          {error && <p className="breakdown-error">{error}</p>}
        </>
      )}

      <div className="modal-footer">
        <button type="button" className="btn-cancel" onClick={onClose}>
          Cancel
        </button>
        {subtasks && (
          <button
            type="button"
            className="btn-save"
            onClick={handleConfirm}
            disabled={saving || validCount === 0}
          >
            {saving ? 'Creating…' : `Create ${validCount} subtask${validCount !== 1 ? 's' : ''}`}
          </button>
        )}
      </div>
    </Modal>
  )
}
