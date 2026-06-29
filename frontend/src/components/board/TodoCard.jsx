import { useState, useEffect, useRef } from 'react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import * as Checkbox from '@radix-ui/react-checkbox'
import { CheckIcon, ChevronUpIcon, ChevronDownIcon } from '@radix-ui/react-icons'
import ConfirmDialog from '../modals/ConfirmDialog'
import AssistModal from '../modals/AssistModal'
import CardSheet from '../modals/CardSheet'
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

function parseGitHubUrl(str) {
  if (!str) return null
  const m = str.match(/^https:\/\/github\.com\/([^/]+\/[^/]+)\/(pull|issues?)\/(\d+)/)
  if (!m) return null
  return { repo: m[1], type: m[2].startsWith('pull') ? 'PR' : 'Issue', number: m[3], url: str }
}

export default function TodoCard({ todo, onEdit, onSave, onDelete, onArchive, onToggle, onMove, isMobile, isOverlay, allTags }) {
  const [expanded, setExpanded] = useState(false)
  const [showSheet, setShowSheet] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [showAssist, setShowAssist] = useState(false)
  const [popping, setPopping] = useState(false)
  const popTimer = useRef(null)

  useEffect(() => {
    if (!expanded) return
    const handler = (e) => { if (e.key === 'Escape') setExpanded(false) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [expanded])

  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: todo.id, disabled: !!isOverlay || !!isMobile })

  // Only flag as overdue if the card has an explicit scheduled date
  const overdueDays = isOverlay ? 0 : (todo.scheduled_at ? (todo.overdue_days ?? 0) : 0)
  const accentColor = overdueDays > 0 ? '#f59e0b' : (SECTION_COLORS[todo.section] ?? '#6b7280')
  if (isDragging && !isOverlay) {
    return (
      <div
        ref={setNodeRef}
        style={{ transform: CSS.Transform.toString(transform), transition }}
        className="column-empty"
      >Drop here</div>
    )
  }

  const style = isOverlay
    ? { boxShadow: 'var(--shadow-lg)', opacity: 1, borderLeftColor: accentColor }
    : { transform: CSS.Transform.toString(transform), transition, borderLeftColor: accentColor }

  const handleCheckboxClick = (e) => {
    e.stopPropagation()
    setPopping(true)
    clearTimeout(popTimer.current)
    popTimer.current = setTimeout(() => setPopping(false), 350)
  }
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
      onClick={() => {
        if (isOverlay) return
        if (window.matchMedia('(max-width: 640px)').matches) setShowSheet(true)
        else setExpanded((v) => !v)
      }}
      {...(isOverlay ? {} : { ...attributes, ...listeners })}
    >
      <div className="event-header">
        <Checkbox.Root
          className={`card-check${popping ? ' card-check--pop' : ''}`}
          checked={todo.completed}
          onCheckedChange={() => onToggle?.(todo)}
          onClick={handleCheckboxClick}
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
      {todo.waiting_reason && (
        <div className="card-waiting-badge">
          &#9203; Waiting: {todo.waiting_reason}
        </div>
      )}

      {expanded && !isOverlay && (
        <div className="event-details">
          {todo.description && (() => {
            const gh = parseGitHubUrl(todo.description)
            return gh ? (
              <a href={gh.url} target="_blank" rel="noopener noreferrer" className="card-github-badge" onClick={(e) => e.stopPropagation()}>
                {gh.repo} #{gh.number} ↗
              </a>
            ) : (
              <div className="event-detail-value">{todo.description}</div>
            )
          })()}
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
            <button
              className="card-action-assist"
              onClick={(e) => { e.stopPropagation(); setShowAssist(true) }}
              title="AI Assistant"
            >
              ✦ Assistant
            </button>
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

      <AssistModal
        open={showAssist}
        onClose={() => setShowAssist(false)}
        task={todo}
      />

      {showSheet && (
        <CardSheet
          card={todo}
          allTags={allTags ?? []}
          onClose={() => setShowSheet(false)}
          onSave={onSave}
          onDelete={onDelete}
          onArchive={onArchive}
          onToggle={onToggle}
          onMove={onMove}
        />
      )}
    </div>
  )
}
