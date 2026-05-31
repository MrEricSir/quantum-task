import { useState, useEffect } from 'react'
import './TodoDetailModal.css'

const SECTION_LABELS = { today: 'Today', week: 'This Week', month: 'This Month', later: 'Later' }
const SECTION_COLORS = { today: '#3b82f6', week: '#8b5cf6', month: '#f59e0b', later: '#6b7280' }

function formatDate(iso) {
  if (!iso) return null
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit',
  })
}

export default function TodoDetailModal({ todo, onClose, onEdit, onDelete, onToggle }) {
  const [confirmDelete, setConfirmDelete] = useState(false)

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal todo-detail-modal" onClick={(e) => e.stopPropagation()}>

        <div className="todo-detail-header">
          <h2 className={`todo-detail-title ${todo.completed ? 'todo-detail-title--done' : ''}`}>
            {todo.title}
          </h2>
          <button className="todo-detail-close" onClick={onClose} aria-label="Close">&#10005;</button>
        </div>

        <div className="todo-detail-body">
          {todo.raw_input && (
            <div className="todo-detail-section">
              <span className="todo-detail-label">Original note</span>
              <div className="raw-input-display">{todo.raw_input}</div>
            </div>
          )}

          {todo.description && (
            <div className="todo-detail-section">
              <span className="todo-detail-label">Description</span>
              <p className="todo-detail-desc">{todo.description}</p>
            </div>
          )}

          <div className="todo-detail-meta">
            <span
              className="todo-detail-section-badge"
              style={{ background: SECTION_COLORS[todo.section] }}
            >
              {SECTION_LABELS[todo.section]}
            </span>
            {todo.scheduled_at && (
              <span className="todo-detail-scheduled">
                &#128337; {formatDate(todo.scheduled_at)}
              </span>
            )}
            {todo.completed && (
              <span className="todo-detail-completed-badge">&#10003; Completed</span>
            )}
          </div>

          {(todo.tags ?? []).length > 0 && (
            <div className="todo-detail-tags">
              {todo.tags.map((tag) => (
                <span key={tag.id} className="todo-detail-tag" style={{ background: tag.color }}>
                  {tag.name}
                </span>
              ))}
            </div>
          )}

          <div className="todo-detail-footer-meta">
            Added {formatDate(todo.created_at)}
            {todo.completed_at && (
              <> &middot; Completed {formatDate(todo.completed_at)}</>
            )}
          </div>
        </div>

        <div className="modal-footer">
          {confirmDelete ? (
            <>
              <span className="todo-detail-confirm-msg">Delete this task?</span>
              <button className="btn-cancel" onClick={() => setConfirmDelete(false)}>Cancel</button>
              <button className="btn-danger" onClick={() => onDelete(todo.id)}>Delete</button>
            </>
          ) : (
            <>
              <button className="btn-danger-ghost" onClick={() => setConfirmDelete(true)}>
                Delete
              </button>
              <div className="todo-detail-spacer" />
              <button className="btn-secondary-action" onClick={() => onToggle(todo)}>
                {todo.completed ? 'Mark Incomplete' : 'Mark Complete'}
              </button>
              <button className="btn-save" onClick={onEdit}>Edit</button>
            </>
          )}
        </div>

      </div>
    </div>
  )
}
