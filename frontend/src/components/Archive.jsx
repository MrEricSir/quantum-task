import { useState } from 'react'
import { CheckIcon, Cross2Icon, ChevronDownIcon, ChevronRightIcon } from '@radix-ui/react-icons'
import './Archive.css'

const SECTION_LABELS = {
  today: 'Today',
  week: 'This Week',
  month: 'This Month',
  later: 'Later',
}

const SECTION_COLORS = {
  today: '#3b82f6',
  week: '#8b5cf6',
  month: '#f59e0b',
  later: '#6b7280',
}

function formatCompletedAt(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export default function Archive({ todos, onDelete, onToggle }) {
  const [open, setOpen] = useState(false)

  const sorted = [...todos].sort((a, b) => {
    if (!a.completed_at && !b.completed_at) return 0
    if (!a.completed_at) return 1
    if (!b.completed_at) return -1
    return new Date(b.completed_at) - new Date(a.completed_at)
  })

  return (
    <div className="archive">
      <button className="archive-toggle" onClick={() => setOpen((v) => !v)}>
        <span className="archive-chevron">{open ? <ChevronDownIcon /> : <ChevronRightIcon />}</span>
        Archive
        {todos.length > 0 && (
          <span className="archive-count">{todos.length}</span>
        )}
      </button>

      {open && (
        <div className="archive-grid">
          {sorted.length === 0 ? (
            <p className="archive-empty">No completed tasks yet.</p>
          ) : (
            sorted.map((todo) => (
              <div key={todo.id} className="archive-card">
                <div className="archive-card-top">
                  <button
                    className="archive-uncheck"
                    onClick={() => onToggle(todo)}
                    title="Restore to board"
                  >
                    <CheckIcon />
                  </button>
                  <span className="archive-card-title">{todo.title}</span>
                  <button
                    className="archive-delete"
                    onClick={() => onDelete(todo.id)}
                    title="Delete"
                  >
                    <Cross2Icon />
                  </button>
                </div>

                {todo.description && (
                  <p className="archive-card-desc">{todo.description}</p>
                )}

                <div className="archive-card-meta">
                  <span
                    className="archive-section-badge"
                    style={{ background: SECTION_COLORS[todo.section] }}
                  >
                    {SECTION_LABELS[todo.section]}
                  </span>
                  {todo.completed_at && (
                    <span className="archive-completed-at">
                      Completed {formatCompletedAt(todo.completed_at)}
                    </span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
