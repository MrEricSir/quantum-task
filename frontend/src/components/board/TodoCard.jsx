import { useState } from 'react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import * as Checkbox from '@radix-ui/react-checkbox'
import { CheckIcon, ChevronUpIcon, ChevronDownIcon } from '@radix-ui/react-icons'
import ConfirmDialog from '../modals/ConfirmDialog'
import './EventCard.css'
import './TodoCard.css'

const SECTIONS = ['today', 'week', 'month', 'later']
const SECTION_LABELS = { today: 'Today', week: 'This Week', month: 'This Month', later: 'Stash' }
const SECTION_COLORS = { today: 'var(--color-today)', week: 'var(--color-week)', month: 'var(--color-month)', later: 'var(--color-later)' }

function formatScheduled(iso) {
  if (!iso) return null
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

function formatDate(iso) {
  if (!iso) return null
  return new Date(iso).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

export default function TodoCard({ todo, onEdit, onDelete, onToggle, onMove, isMobile, isOverlay }) {
  const [expanded, setExpanded] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)

  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: todo.id, disabled: !!isOverlay || !!isMobile })

  // Only flag as overdue if the card has an explicit scheduled date
  const overdueDays = isOverlay ? 0 : (todo.scheduled_at ? (todo.overdue_days ?? 0) : 0)
  const accentColor = overdueDays > 0 ? '#f59e0b' : (SECTION_COLORS[todo.section] ?? '#6b7280')
  const style = isOverlay
    ? { boxShadow: 'var(--shadow-lg)', opacity: 1, borderLeftColor: accentColor }
    : { transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.35 : 1, borderLeftColor: accentColor }

  const handleToggle = (e) => { e.stopPropagation(); onToggle?.(todo) }
  const handleEdit   = (e) => { e.stopPropagation(); setExpanded(false); onEdit?.(todo) }
  const handleDelete = (e) => { e.stopPropagation(); setShowConfirm(true) }
  const handleMove   = (e, section) => { e.stopPropagation(); setExpanded(false); onMove?.(todo.id, section) }

  return (
    <div
      ref={isOverlay ? undefined : setNodeRef}
      style={style}
      className={[
        'event-card',
        expanded ? 'event-card--expanded' : '',
        todo.completed ? 'event-card--done' : '',
        overdueDays > 0 ? 'event-card--overdue' : '',
        isDragging ? 'event-card--dragging' : '',
        isOverlay ? 'event-card--overlay' : '',
      ].filter(Boolean).join(' ')}
      onClick={() => !isOverlay && setExpanded((v) => !v)}
      {...(isOverlay ? {} : { ...attributes, ...listeners })}
    >
      <div className="event-header">
        <Checkbox.Root
          className="card-check"
          checked={todo.completed}
          onCheckedChange={() => onToggle?.(todo)}
          onClick={(e) => e.stopPropagation()}
          title={todo.completed ? 'Mark incomplete' : 'Mark complete'}
          aria-label="toggle complete"
        >
          <Checkbox.Indicator className="card-check-indicator">
            <CheckIcon />
          </Checkbox.Indicator>
        </Checkbox.Root>
        <span className="event-title">{todo.title}</span>
        {!isOverlay && (
          <span className="event-chevron">
            {expanded ? <ChevronUpIcon /> : <ChevronDownIcon />}
          </span>
        )}
      </div>

      {(todo.scheduled_at || todo.recurrence_rule) && (
        <div className="event-time">
          {todo.scheduled_at && <><span className="clock-icon">&#128337;</span>{formatScheduled(todo.scheduled_at)}</>}
          {todo.recurrence_rule && (
            <span className="card-recurrence">&#8635; {todo.recurrence_rule}</span>
          )}
        </div>
      )}
      {overdueDays > 0 && (
        <div className="card-overdue-badge">
          &#9888; {overdueDays === 1 ? '1 day overdue' : `${overdueDays} days overdue`}
        </div>
      )}

      {expanded && !isOverlay && (
        <div className="event-details">
          {todo.description && (
            <div className="event-detail-value">{todo.description}</div>
          )}
          <div className="card-move-row">
            <span className="event-detail-label">Move to</span>
            <div className="card-move-buttons">
              {SECTIONS.filter((s) => s !== todo.section).map((s) => (
                <button
                  key={s}
                  className="card-move-btn"
                  style={{ borderColor: SECTION_COLORS[s], color: SECTION_COLORS[s] }}
                  onClick={(e) => handleMove(e, s)}
                >
                  {SECTION_LABELS[s]}
                </button>
              ))}
            </div>
          </div>
          <div className="card-detail-actions">
            <button className="card-action-edit" onClick={handleEdit}>Edit</button>
          </div>
        </div>
      )}

      {(todo.tags ?? []).length > 0 && (
        <div className="event-tags">
          {(todo.tags ?? []).map((tag) => (
            <span key={tag.id} className="event-tag-pill" style={{ background: tag.color }}>
              {tag.name}
            </span>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={showConfirm}
        title="Delete card?"
        description={`"${todo.title}" will be permanently deleted.`}
        onConfirm={() => { setShowConfirm(false); onDelete?.(todo.id) }}
        onCancel={() => setShowConfirm(false)}
      />
    </div>
  )
}
