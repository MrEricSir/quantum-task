import { useState } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import * as Dialog from '@radix-ui/react-dialog'
import { Pencil2Icon, TrashIcon, ArrowUpIcon, PlusIcon, Cross2Icon } from '@radix-ui/react-icons'
import Modal from './Modal'
import './NotesPage.css'

marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text))
}

function timeAgo(iso) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function NoteEditorModal({ note, allTags, onSave, onDelete, onClose }) {
  const isNew = !note?.id
  const [title, setTitle] = useState(note?.title ?? '')
  const [content, setContent] = useState(note?.content ?? '')
  const [selectedTagIds, setSelectedTagIds] = useState(() => (note?.tags ?? []).map((t) => t.id))
  const [saving, setSaving] = useState(false)

  const toggleTag = (id) => setSelectedTagIds((prev) =>
    prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
  )

  const handleSave = async () => {
    if (!content.trim() && !title.trim()) return
    setSaving(true)
    await onSave({ title: title.trim() || null, content: content.trim(), tag_ids: selectedTagIds })
    onClose()
  }

  const handleDelete = async () => {
    if (!window.confirm('Delete this note?')) return
    await onDelete(note.id)
    onClose()
  }

  return (
    <Modal onClose={onClose} className="note-editor-modal">
      <Dialog.Title asChild>
        <h2>{isNew ? 'New Note' : 'Edit Note'}</h2>
      </Dialog.Title>

      <div className="form-group">
        <label htmlFor="note-title">Title <span className="note-optional">(optional)</span></label>
        <input
          id="note-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Untitled"
          autoFocus={isNew}
        />
      </div>

      <div className="form-group">
        <label htmlFor="note-content">Content</label>
        <textarea
          id="note-content"
          className="note-content-input"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Write anything — markdown is supported"
          rows={10}
          autoFocus={!isNew}
        />
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
          <button className="btn-danger" onClick={handleDelete} type="button">
            Delete
          </button>
        )}
        <button className="btn-cancel" onClick={onClose}>Cancel</button>
        <button
          className="btn-save"
          onClick={handleSave}
          disabled={saving || (!content.trim() && !title.trim())}
        >
          {saving ? 'Saving…' : isNew ? 'Create' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}

function NoteCard({ note, onEdit, onPromote }) {
  const displayTitle = note.title || (note.content?.split('\n')[0]?.replace(/^#+\s*/, '').slice(0, 80) || 'Untitled')
  const hasBody = note.content && note.content.trim()

  return (
    <div className="note-card" onClick={() => onEdit(note)} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onEdit(note)}>
      <div className="note-card-header">
        <span className="note-card-title">{displayTitle}</span>
        <span className="note-card-age">{timeAgo(note.updated_at)}</span>
      </div>

      {hasBody && !note.title && (
        // Only show preview if there's more content beyond the title line
        note.content.includes('\n') && (
          <div
            className="note-card-preview"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(note.content.split('\n').slice(1).join('\n').trim().slice(0, 300)) }}
          />
        )
      )}
      {hasBody && note.title && (
        <div
          className="note-card-preview"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(note.content.slice(0, 300)) }}
        />
      )}

      {note.tags.length > 0 && (
        <div className="note-card-tags">
          {note.tags.map((tag) => (
            <span key={tag.id} className="note-card-tag" style={{ background: tag.color }}>
              {tag.name}
            </span>
          ))}
        </div>
      )}

      <div className="note-card-actions" onClick={(e) => e.stopPropagation()}>
        <button
          className="note-action-btn"
          onClick={() => onEdit(note)}
          title="Edit"
          aria-label="Edit note"
        >
          <Pencil2Icon />
        </button>
        <button
          className="note-action-btn"
          onClick={() => onPromote(note.id)}
          title="Promote to task"
          aria-label="Promote to task"
        >
          <ArrowUpIcon />
        </button>
      </div>
    </div>
  )
}

export default function NotesPage({ notes, allTags, onAdd, onUpdate, onDelete, onPromote }) {
  const [editingNote, setEditingNote] = useState(null)
  const [showEditor, setShowEditor] = useState(false)
  const [promoteMsg, setPromoteMsg] = useState('')

  const openNew = () => {
    setEditingNote(null)
    setShowEditor(true)
  }

  const openEdit = (note) => {
    setEditingNote(note)
    setShowEditor(true)
  }

  const handleSave = async (data) => {
    if (editingNote?.id) {
      await onUpdate(editingNote.id, data)
    } else {
      await onAdd(data)
    }
  }

  const handlePromote = async (id) => {
    await onPromote(id)
    setPromoteMsg('Added to Later tasks')
    setTimeout(() => setPromoteMsg(''), 2500)
  }

  return (
    <div className="notes-page">
      <div className="notes-header">
        <h2 className="notes-title">Notes</h2>
        <button className="notes-new-btn" onClick={openNew} type="button">
          <PlusIcon /> New note
        </button>
      </div>

      {promoteMsg && (
        <div className="notes-toast">{promoteMsg}</div>
      )}

      {notes.length === 0 ? (
        <div className="notes-empty">
          <p>No notes yet.</p>
          <button className="notes-empty-btn" onClick={openNew}>Create your first note</button>
        </div>
      ) : (
        <div className="notes-grid">
          {notes.map((note) => (
            <NoteCard
              key={note.id}
              note={note}
              onEdit={openEdit}
              onPromote={handlePromote}
            />
          ))}
        </div>
      )}

      {showEditor && (
        <NoteEditorModal
          note={editingNote}
          allTags={allTags}
          onSave={handleSave}
          onDelete={onDelete}
          onClose={() => setShowEditor(false)}
        />
      )}
    </div>
  )
}
