import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { PlusIcon, Cross2Icon } from '@radix-ui/react-icons'
import Modal from './Modal'
import Collapsible from './Collapsible'
import './NotesPage.css'

function timeAgo(iso) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function noteTitle(note) {
  return note.title || note.content?.split('\n')[0]?.trim().slice(0, 100) || 'New note'
}

function notePreview(note) {
  const lines = note.title
    ? (note.content || '').split('\n')
    : (note.content || '').split('\n').slice(1)
  return lines.filter((l) => l.trim()).join(' ').trim().slice(0, 140)
}

// ── Note editor modal ──────────────────────────────────────────────────────

function NoteEditorModal({ note, allTags, onSave, onDelete, onArchive, onPromote, onClose }) {
  const isNew = !note?.id
  const [content, setContent] = useState(note?.content ?? '')
  const [selectedTagIds, setSelectedTagIds] = useState(() => (note?.tags ?? []).map((t) => t.id))
  const [saving, setSaving] = useState(false)

  const toggleTag = (id) =>
    setSelectedTagIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

  const handleSave = async () => {
    if (!content.trim()) return
    setSaving(true)
    await onSave({ title: null, content: content.trim(), tag_ids: selectedTagIds })
    onClose()
  }

  const handleDelete = async () => {
    if (!window.confirm('Delete this note?')) return
    await onDelete(note.id)
    onClose()
  }

  return (
    <Modal onClose={onClose} className="modal--md note-editor-modal">
      <Dialog.Title asChild>
        <h2>{isNew ? 'New Note' : 'Edit Note'}</h2>
      </Dialog.Title>

      <textarea
        id="note-content"
        className="note-content-input"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="Start typing..."
        rows={12}
        autoFocus
      />

      {allTags.length > 0 && (
        <div className="form-group">
          <label>Tags</label>
          <div className="note-tag-row">
            {allTags.map((tag) => {
              const on = selectedTagIds.includes(tag.id)
              return (
                <button
                  key={tag.id}
                  type="button"
                  className={`tag-toggle ${on ? 'tag-toggle--on' : ''}`}
                  style={on
                    ? { background: tag.color, borderColor: tag.color, color: '#fff' }
                    : { borderColor: tag.color, color: tag.color }}
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
        {!isNew && (
          <button className="btn-danger" onClick={handleDelete} type="button" style={{ marginRight: 'auto' }}>
            Delete
          </button>
        )}
        {!isNew && onArchive && (
          <button className="btn-secondary" onClick={() => { onArchive(note.id); onClose() }} type="button">
            Archive
          </button>
        )}
        {!isNew && onPromote && (
          <button className="btn-secondary" onClick={async () => { await onPromote(note.id); await onDelete(note.id); onClose() }} type="button">
            Make task
          </button>
        )}
        <button className="btn-cancel" onClick={onClose}>Cancel</button>
        <button className="btn-save" onClick={handleSave} disabled={saving || !content.trim()}>
          {saving ? 'Saving…' : isNew ? 'Create' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}

// ── Notes archive ──────────────────────────────────────────────────────────

function NotesArchive({ notes, onUnarchive, onDelete }) {
  if (notes.length === 0) return null
  return (
    <div className="notes-archive">
      <Collapsible label="Note Archive" count={notes.length} defaultOpen={true}>
        <div className="notes-archive-list">
          {notes.map((note) => (
            <div key={note.id} className="notes-archive-row">
              <span className="notes-archive-row-title">{noteTitle(note)}</span>
              <div className="notes-archive-row-actions">
                <button className="notes-archive-btn" onClick={() => onUnarchive(note.id)}>Restore</button>
                <button
                  className="notes-archive-btn notes-archive-btn--delete"
                  onClick={() => onDelete(note.id)}
                  aria-label="Delete note"
                >
                  <Cross2Icon />
                </button>
              </div>
            </div>
          ))}
        </div>
      </Collapsible>
    </div>
  )
}

// ── NotesPage ──────────────────────────────────────────────────────────────

export default function NotesPage({ notes, archivedNotes = [], allTags, onAdd, onUpdate, onDelete, onPromote, onArchive, onUnarchive }) {
  const [editingNote, setEditingNote] = useState(null)
  const [showEditor, setShowEditor] = useState(false)

  const openEdit = (note) => { setEditingNote(note); setShowEditor(true) }
  const openNew = () => { setEditingNote(null); setShowEditor(true) }

  const handleSave = async (data) => {
    if (editingNote?.id) {
      await onUpdate(editingNote.id, data)
    } else {
      await onAdd(data)
    }
  }

  return (
    <div className="notes-page">
      <div className="notes-header">
        <h2 className="notes-title">Notes</h2>
        <button className="notes-new-btn" onClick={openNew} type="button">
          <PlusIcon /> New note
        </button>
      </div>

      {notes.length === 0 ? (
        <div className="notes-empty">
          <p>No notes yet.</p>
          <button className="notes-empty-btn" onClick={openNew}>Write your first note</button>
        </div>
      ) : (
        <div className="notes-list">
          {notes.map((note) => {
            const preview = notePreview(note)
            return (
              <div
                key={note.id}
                className="note-row"
                onClick={() => openEdit(note)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && openEdit(note)}
              >
                <div className="note-row-body">
                  <div className="note-row-title">{noteTitle(note)}</div>
                  {preview && <div className="note-row-preview">{preview}</div>}
                </div>
                <div className="note-row-age">{timeAgo(note.updated_at)}</div>
              </div>
            )
          })}
        </div>
      )}

      <NotesArchive notes={archivedNotes} onUnarchive={onUnarchive} onDelete={onDelete} />

      {showEditor && (
        <NoteEditorModal
          note={editingNote}
          allTags={allTags}
          onSave={handleSave}
          onDelete={onDelete}
          onArchive={onArchive}
          onPromote={onPromote}
          onClose={() => setShowEditor(false)}
        />
      )}
    </div>
  )
}
