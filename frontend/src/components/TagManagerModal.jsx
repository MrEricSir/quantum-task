import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Pencil1Icon, Cross2Icon } from '@radix-ui/react-icons'
import Modal from './Modal'
import './TagManagerModal.css'

const PRESET_COLORS = [
  '#3b82f6', // blue
  '#8b5cf6', // purple
  '#ec4899', // pink
  '#ef4444', // red
  '#f59e0b', // amber
  '#10b981', // emerald
  '#14b8a6', // teal
  '#6b7280', // gray
]

export default function TagManagerModal({ tags, todos = [], onClose, onCreate, onUpdate, onDelete, onReplace }) {
  const [name, setName] = useState('')
  const [color, setColor] = useState(PRESET_COLORS[0])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const [editColor, setEditColor] = useState(PRESET_COLORS[0])
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState(null)
  const [moveToTagId, setMoveToTagId] = useState('')
  const [deleteSaving, setDeleteSaving] = useState(false)

  const startEdit = (tag) => {
    setEditingId(tag.id)
    setEditName(tag.name)
    setEditColor(tag.color)
    setEditError('')
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditError('')
  }

  const handleSaveEdit = async (tag) => {
    if (!editName.trim()) { setEditError('Name is required.'); return }
    setEditSaving(true)
    setEditError('')
    try {
      await onUpdate(tag.id, { name: editName.trim(), color: editColor })
      setEditingId(null)
    } catch (err) {
      setEditError(err.message.includes('already exists') ? 'A tag with that name already exists.' : 'Something went wrong.')
    } finally {
      setEditSaving(false)
    }
  }

  const todosForTag = (tagId) =>
    todos.filter((t) => (t.tags ?? []).some((tg) => tg.id === tagId))

  const handleDeleteClick = (tag) => {
    const count = todosForTag(tag.id).length
    if (count === 0) {
      onDelete(tag.id)
    } else {
      setConfirmDeleteId(tag.id)
      setMoveToTagId('')
    }
  }

  const handleConfirmDelete = async () => {
    setDeleteSaving(true)
    try {
      if (moveToTagId) {
        await onReplace(confirmDeleteId, Number(moveToTagId))
      } else {
        await onDelete(confirmDeleteId)
      }
      setConfirmDeleteId(null)
    } finally {
      setDeleteSaving(false)
    }
  }

  const handleCreate = async (e) => {
    e.preventDefault()
    if (!name.trim()) { setError('Name is required.'); return }
    setSaving(true)
    setError('')
    try {
      await onCreate({ name: name.trim(), color })
      setName('')
      setColor(PRESET_COLORS[0])
    } catch (err) {
      setError(err.message.includes('409') || err.message.toLowerCase().includes('exists')
        ? 'A tag with that name already exists.'
        : 'Something went wrong.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} className="tag-mgr-modal">
      <Dialog.Title asChild><h2>Manage Tags</h2></Dialog.Title>

        {/* Existing tags */}
        <div className="tag-mgr-list">
          {tags.length === 0 && (
            <p className="tag-mgr-empty">No tags yet.</p>
          )}
          {tags.map((tag) => {
            const usageCount = todosForTag(tag.id).length
            if (editingId === tag.id) {
              return (
                <div key={tag.id} className="tag-mgr-row tag-mgr-row--editing">
                  <div className="tag-mgr-edit-colors">
                    {PRESET_COLORS.map((c) => (
                      <button
                        key={c}
                        type="button"
                        className={`color-swatch color-swatch--sm ${editColor === c ? 'color-swatch--active' : ''}`}
                        style={{ background: c }}
                        onClick={() => setEditColor(c)}
                        title={c}
                      />
                    ))}
                  </div>
                  <input
                    className="tag-mgr-edit-input"
                    value={editName}
                    onChange={(e) => { setEditName(e.target.value); setEditError('') }}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleSaveEdit(tag) }}
                    maxLength={32}
                    autoFocus
                  />
                  {editError && <span className="tag-mgr-edit-error">{editError}</span>}
                  <button
                    className="tag-mgr-save"
                    onClick={() => handleSaveEdit(tag)}
                    disabled={editSaving || !editName.trim()}
                    title="Save"
                  >
                    {editSaving ? '…' : '✓'}
                  </button>
                  <button className="tag-mgr-cancel" onClick={cancelEdit} title="Cancel"><Cross2Icon /></button>
                </div>
              )
            }
            if (confirmDeleteId === tag.id) {
              const otherTags = tags.filter((t) => t.id !== tag.id)
              return (
                <div key={tag.id} className="tag-mgr-row tag-mgr-row--confirm">
                  <span className="tag-mgr-confirm-msg">
                    {usageCount} {usageCount === 1 ? 'task uses' : 'tasks use'} <strong>{tag.name}</strong>. Move them to:
                  </span>
                  <select
                    className="tag-mgr-move-select"
                    value={moveToTagId}
                    onChange={(e) => setMoveToTagId(e.target.value)}
                  >
                    <option value="">— remove tag —</option>
                    {otherTags.map((t) => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </select>
                  <div className="tag-mgr-confirm-actions">
                    <button
                      className="tag-mgr-confirm-delete"
                      onClick={handleConfirmDelete}
                      disabled={deleteSaving}
                    >
                      {deleteSaving ? '…' : moveToTagId ? 'Move & Delete' : 'Delete'}
                    </button>
                    <button className="tag-mgr-cancel" onClick={() => setConfirmDeleteId(null)}><Cross2Icon /> Cancel</button>
                  </div>
                </div>
              )
            }
            return (
              <div key={tag.id} className="tag-mgr-row">
                <span className="tag-mgr-dot" style={{ background: tag.color }} />
                <span className="tag-mgr-name">{tag.name}</span>
                {usageCount > 0 && (
                  <span className="tag-mgr-count">{usageCount}</span>
                )}
                <button
                  className="tag-mgr-edit"
                  onClick={() => startEdit(tag)}
                  title="Edit tag"
                >
                  <Pencil1Icon />
                </button>
                <button
                  className="tag-mgr-delete"
                  onClick={() => handleDeleteClick(tag)}
                  title="Delete tag"
                >
                  <Cross2Icon />
                </button>
              </div>
            )
          })}
        </div>

        {/* Add new tag */}
        <form className="tag-mgr-form" onSubmit={handleCreate} noValidate>
          <p className="tag-mgr-form-label">New tag</p>

          <div className="tag-mgr-color-row">
            {PRESET_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                className={`color-swatch ${color === c ? 'color-swatch--active' : ''}`}
                style={{ background: c }}
                onClick={() => setColor(c)}
                title={c}
              />
            ))}
          </div>

          <div className="tag-mgr-input-row">
            <input
              type="text"
              value={name}
              onChange={(e) => { setName(e.target.value); setError('') }}
              placeholder="Tag name"
              maxLength={32}
            />
            <button type="submit" className="btn-save" disabled={saving || !name.trim()}>
              {saving ? '…' : 'Add'}
            </button>
          </div>
          {error && <span className="form-error">{error}</span>}
        </form>

        <div className="modal-footer">
          <button className="btn-cancel" onClick={onClose}>Done</button>
        </div>
    </Modal>
  )
}
