import { useState } from 'react'
import { PlusIcon, Cross2Icon } from '@radix-ui/react-icons'
import AddTodoModal from './AddTodoModal'
import Collapsible from './Collapsible'
import './NotesPage.css'

function timeAgo(iso) {
  if (!iso) return ''
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function cardTitle(card) {
  return card.title || card.description?.split('\n')[0]?.trim().slice(0, 100) || 'Untitled'
}

function cardPreview(card) {
  // description is the full text content of a reference card
  const lines = (card.description || '').split('\n')
  // Skip first line if the title was derived from it
  const start = card.title ? 0 : 1
  return lines.slice(start).filter((l) => l.trim()).join(' ').trim().slice(0, 140)
}

// ── Cards archive ───────────────────────────────────────────────────────────

function CardsArchive({ cards, onUnarchive, onDelete }) {
  if (cards.length === 0) return null
  return (
    <div className="notes-archive">
      <Collapsible label="Card Archive" count={cards.length} defaultOpen={true}>
        <div className="notes-archive-list">
          {cards.map((card) => (
            <div key={card.id} className="notes-archive-row">
              <span className="notes-archive-row-title">{cardTitle(card)}</span>
              <div className="notes-archive-row-actions">
                <button className="notes-archive-btn" onClick={() => onUnarchive(card.id)}>Restore</button>
                <button
                  className="notes-archive-btn notes-archive-btn--delete"
                  onClick={() => onDelete(card.id)}
                  aria-label="Delete card"
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

// ── CardsPage ───────────────────────────────────────────────────────────────

export default function CardsPage({ cards, archivedCards = [], allTags, onAdd, onUpdate, onDelete, onArchive, onUnarchive }) {
  const [editingCard, setEditingCard] = useState(null)
  const [showEditor, setShowEditor] = useState(false)

  const openEdit = (card) => { setEditingCard(card); setShowEditor(true) }
  const openNew = () => { setEditingCard(null); setShowEditor(true) }

  const handleSave = async (data) => {
    if (editingCard?.id) {
      await onUpdate(editingCard.id, data)
    } else {
      await onAdd(data)
    }
  }

  return (
    <div className="notes-page">
      <div className="notes-header">
        <h2 className="notes-title">Reference</h2>
        <button className="notes-new-btn" onClick={openNew} type="button">
          <PlusIcon /> New card
        </button>
      </div>

      {cards.length === 0 ? (
        <div className="notes-empty">
          <p>No cards yet.</p>
          <button className="notes-empty-btn" onClick={openNew}>Create your first card</button>
        </div>
      ) : (
        <div className="notes-list">
          {cards.map((card) => {
            const preview = cardPreview(card)
            return (
              <div
                key={card.id}
                className="note-row"
                onClick={() => openEdit(card)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && openEdit(card)}
              >
                <div className="note-row-body">
                  <div className="note-row-title">{cardTitle(card)}</div>
                  {preview && <div className="note-row-preview">{preview}</div>}
                </div>
                <div className="note-row-age">{timeAgo(card.updated_at ?? card.created_at)}</div>
              </div>
            )
          })}
        </div>
      )}

      <CardsArchive cards={archivedCards} onUnarchive={onUnarchive} onDelete={onDelete} />

      {showEditor && (
        <AddTodoModal
          card={editingCard}
          defaultSection="none"
          allTags={allTags}
          onSave={async (data) => { await handleSave(data); setShowEditor(false) }}
          onDelete={editingCard ? async () => { await onDelete(editingCard.id); setShowEditor(false) } : undefined}
          onArchive={editingCard ? async () => { await onArchive(editingCard.id); setShowEditor(false) } : undefined}
          onClose={() => setShowEditor(false)}
        />
      )}
    </div>
  )
}
