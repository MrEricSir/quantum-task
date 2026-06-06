import { useState, useRef } from 'react'
import { marked, Renderer } from 'marked'
import DOMPurify from 'dompurify'
import * as Dialog from '@radix-ui/react-dialog'
import { Pencil2Icon, TrashIcon, ArrowUpIcon, PlusIcon, Cross2Icon, ChevronDownIcon, ChevronRightIcon } from '@radix-ui/react-icons'
import Modal from './Modal'
import './NotesPage.css'

// Custom renderer: checkboxes without `disabled` so they can be tapped.
const _renderer = new Renderer()
_renderer.checkbox = ({ checked }) => `<input type="checkbox"${checked ? ' checked' : ''}> `
marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text, { renderer: _renderer }), {
    ADD_TAGS: ['input'],
    ADD_ATTR: ['type', 'checked'],
  })
}

// Toggle the nth `- [ ]` / `- [x]` in raw markdown content.
function toggleNthCheckbox(content, idx) {
  let count = -1
  return content.replace(/- \[[ x]\]/gi, (match) => {
    count++
    if (count === idx) return match[3].toLowerCase() === 'x' ? '- [ ]' : '- [x]'
    return match
  })
}

// Returns true if the content has at least one checkbox and all are checked.
function allCheckboxesChecked(content) {
  if (!content) return false
  const items = content.match(/- \[[ x]\]/gi) || []
  return items.length > 0 && items.every((m) => m[3].toLowerCase() === 'x')
}

function timeAgo(iso) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function noteDisplayTitle(note) {
  return note.title || note.content?.split('\n')[0]?.replace(/^#+\s*/, '').slice(0, 80) || 'Untitled'
}

// ── Note view modal (read-only, interactive checkboxes) ────────────────────

function NoteViewModal({ note, onClose, onEdit, onArchive, onCheckboxToggle }) {
  const handleContentClick = (e) => {
    if (e.target.type === 'checkbox') {
      e.stopPropagation()
      const all = [...e.currentTarget.querySelectorAll('input[type="checkbox"]')]
      const idx = all.indexOf(e.target)
      if (idx !== -1) onCheckboxToggle(note, idx)
    }
  }

  return (
    <Modal onClose={onClose} className="note-view-modal">
      <div className="note-view-header">
        <Dialog.Title asChild>
          <h2 className="note-view-title">{noteDisplayTitle(note)}</h2>
        </Dialog.Title>
        <div className="note-view-header-actions">
          <button className="btn-secondary note-view-edit-btn" onClick={onEdit} aria-label="Edit note">
            Edit
          </button>
          <button className="note-view-close-btn" onClick={onClose} aria-label="Close">
            <Cross2Icon />
          </button>
        </div>
      </div>

      <div
        className="note-view-content"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(note.content) }}
        onClick={handleContentClick}
      />

      {note.tags?.length > 0 && (
        <div className="note-view-tags">
          {note.tags.map((tag) => (
            <span key={tag.id} className="note-card-tag" style={{ background: tag.color }}>{tag.name}</span>
          ))}
        </div>
      )}

      <div className="note-view-footer">
        <span className="note-view-age">{timeAgo(note.updated_at)}</span>
        <button className="note-view-archive-btn" onClick={onArchive}>
          Archive
        </button>
      </div>
    </Modal>
  )
}

// ── Note editor modal ──────────────────────────────────────────────────────

function NoteEditorModal({ note, allTags, onSave, onDelete, onClose }) {
  const isNew = !note?.id
  const [title, setTitle] = useState(note?.title ?? '')
  const [content, setContent] = useState(note?.content ?? '')
  const [selectedTagIds, setSelectedTagIds] = useState(() => (note?.tags ?? []).map((t) => t.id))
  const [saving, setSaving] = useState(false)
  const textareaRef = useRef(null)

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

  const insertLinePrefix = (prefix) => {
    const ta = textareaRef.current
    if (!ta) return
    const pos = ta.selectionStart
    const lineStart = content.lastIndexOf('\n', pos - 1) + 1
    const needsNewline = pos > lineStart && content.slice(lineStart, pos).trim().length > 0
    const insert = needsNewline ? '\n' + prefix : prefix
    const insertAt = needsNewline ? pos : lineStart
    const newVal = content.slice(0, insertAt) + insert + content.slice(insertAt)
    setContent(newVal)
    const newPos = insertAt + insert.length
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(newPos, newPos) })
  }

  const wrapSelection = (wrapper) => {
    const ta = textareaRef.current
    if (!ta) return
    const { selectionStart: s, selectionEnd: e } = ta
    const selected = content.slice(s, e)
    const newVal = content.slice(0, s) + wrapper + selected + wrapper + content.slice(e)
    setContent(newVal)
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(s + wrapper.length, e + wrapper.length) })
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
        <div className="note-toolbar">
          <button type="button" className="note-toolbar-btn" onClick={() => insertLinePrefix('- [ ] ')} title="Checklist item">
            ☐
          </button>
          <button type="button" className="note-toolbar-btn" onClick={() => insertLinePrefix('- ')} title="List item">
            •
          </button>
          <button type="button" className="note-toolbar-btn note-toolbar-bold" onClick={() => wrapSelection('**')} title="Bold">
            B
          </button>
        </div>
        <textarea
          ref={textareaRef}
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

// ── Note card ─────────────────────────────────────────────────────────────

function NoteCard({ note, onView, onEdit, onPromote, onCheckboxToggle }) {
  const displayTitle = noteDisplayTitle(note)
  const hasBody = note.content && note.content.trim()

  const handlePreviewClick = (e) => {
    if (e.target.type === 'checkbox') {
      e.stopPropagation()
      const all = [...e.currentTarget.querySelectorAll('input[type="checkbox"]')]
      const idx = all.indexOf(e.target)
      if (idx !== -1) onCheckboxToggle(note, idx)
    }
  }

  return (
    <div className="note-card" onClick={() => onView(note)} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onView(note)}>
      <div className="note-card-header">
        <span className="note-card-title">{displayTitle}</span>
        <span className="note-card-age">{timeAgo(note.updated_at)}</span>
      </div>

      {hasBody && !note.title && (
        note.content.includes('\n') && (
          <div
            className="note-card-preview"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(note.content.split('\n').slice(1).join('\n').trim().slice(0, 300)) }}
            onClick={handlePreviewClick}
          />
        )
      )}
      {hasBody && note.title && (
        <div
          className="note-card-preview"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(note.content.slice(0, 300)) }}
          onClick={handlePreviewClick}
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

// ── Notes archive ──────────────────────────────────────────────────────────

function NotesArchive({ notes, onUnarchive, onDelete }) {
  const [open, setOpen] = useState(true)
  if (notes.length === 0) return null

  return (
    <div className="notes-archive">
      <button className="notes-archive-toggle" onClick={() => setOpen((v) => !v)}>
        <span className="notes-archive-chevron">{open ? <ChevronDownIcon /> : <ChevronRightIcon />}</span>
        Note Archive
        <span className="notes-archive-count">{notes.length}</span>
      </button>
      {open && (
        <div className="notes-archive-list">
          {notes.map((note) => (
            <div key={note.id} className="notes-archive-row">
              <span className="notes-archive-row-title">{noteDisplayTitle(note)}</span>
              <div className="notes-archive-row-actions">
                <button
                  className="notes-archive-btn"
                  onClick={() => onUnarchive(note.id)}
                  title="Restore note"
                >
                  Restore
                </button>
                <button
                  className="notes-archive-btn notes-archive-btn--delete"
                  onClick={() => onDelete(note.id)}
                  title="Delete permanently"
                  aria-label="Delete note"
                >
                  <Cross2Icon />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── NotesPage ──────────────────────────────────────────────────────────────

export default function NotesPage({ notes, archivedNotes = [], allTags, onAdd, onUpdate, onDelete, onPromote, onArchive, onUnarchive }) {
  const [viewingNote, setViewingNote] = useState(null)
  const [editingNote, setEditingNote] = useState(null)
  const [showEditor, setShowEditor] = useState(false)
  const [promoteMsg, setPromoteMsg] = useState('')

  const openView = (note) => setViewingNote(note)
  const closeView = () => setViewingNote(null)

  const openEdit = (note) => {
    setViewingNote(null)
    setEditingNote(note)
    setShowEditor(true)
  }

  const openNew = () => {
    setEditingNote(null)
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

  // Card-level checkbox toggle (no view modal open)
  const handleCardCheckboxToggle = async (note, idx) => {
    const newContent = toggleNthCheckbox(note.content, idx)
    await onUpdate(note.id, { title: note.title, content: newContent, tag_ids: note.tags.map((t) => t.id) })
    if (allCheckboxesChecked(newContent)) {
      await onArchive(note.id)
    }
  }

  // Checkbox toggle inside the view modal — keep modal in sync, then maybe archive
  const handleViewCheckboxToggle = async (note, idx) => {
    const newContent = toggleNthCheckbox(note.content, idx)
    setViewingNote((prev) => prev?.id === note.id ? { ...prev, content: newContent } : prev)
    await onUpdate(note.id, { title: note.title, content: newContent, tag_ids: note.tags.map((t) => t.id) })
    if (allCheckboxesChecked(newContent)) {
      setViewingNote(null)
      await onArchive(note.id)
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
              onView={openView}
              onEdit={openEdit}
              onPromote={handlePromote}
              onCheckboxToggle={handleCardCheckboxToggle}
            />
          ))}
        </div>
      )}

      <NotesArchive
        notes={archivedNotes}
        onUnarchive={onUnarchive}
        onDelete={onDelete}
      />

      {viewingNote && (
        <NoteViewModal
          note={viewingNote}
          onClose={closeView}
          onEdit={() => openEdit(viewingNote)}
          onArchive={() => { closeView(); onArchive(viewingNote.id) }}
          onCheckboxToggle={handleViewCheckboxToggle}
        />
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
