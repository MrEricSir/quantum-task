import { useState, useEffect, useRef } from 'react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import * as Checkbox from '@radix-ui/react-checkbox'
import { CheckIcon, ChevronUpIcon, ChevronDownIcon } from '@radix-ui/react-icons'
import ConfirmDialog from '../modals/ConfirmDialog'
import AssistModal from '../modals/AssistModal'
import CardSheet from '../modals/CardSheet'
import './EventCard.css'
import './Card.css'
import { SECTIONS, SECTION_LABELS } from '../../lib/sections'

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

export default function Card({ card, onEdit, onSave, onDelete, onArchive, onToggle, onMove, isMobile, isOverlay, allTags, onBreakdown }) {
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
    useSortable({ id: card.id, disabled: !!isOverlay || !!isMobile })

  // Only flag as overdue if the card has an explicit scheduled date
  const overdueDays = isOverlay ? 0 : (card.scheduled_at ? (card.overdue_days ?? 0) : 0)
  const accentColor = overdueDays > 0 ? '#f59e0b' : (SECTION_COLORS[card.section] ?? '#6b7280')
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
  const handleEdit   = (e) => { e.stopPropagation(); setExpanded(false); onEdit?.(card) }
  const handleDelete = (e) => { e.stopPropagation(); setShowConfirm(true) }
  const handleMove   = (e, section) => { e.stopPropagation(); setExpanded(false); onMove?.(card.id, section) }

  return (
    <div
      ref={isOverlay ? undefined : setNodeRef}
      style={style}
      className={[
        'event-card',
        expanded ? 'event-card--expanded' : '',
        card.completed ? 'event-card--done' : '',
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
          checked={card.completed}
          onCheckedChange={() => onToggle?.(card)}
          onClick={handleCheckboxClick}
          title={card.completed ? 'Mark incomplete' : 'Mark complete'}
          aria-label="toggle complete"
        >
          <Checkbox.Indicator className="card-check-indicator">
            <CheckIcon />
          </Checkbox.Indicator>
        </Checkbox.Root>
        <span className="event-title">{card.title}</span>
        {!isOverlay && (
          <span className="event-chevron">
            {expanded ? <ChevronUpIcon /> : <ChevronDownIcon />}
          </span>
        )}
      </div>

      {(card.scheduled_at || card.recurrence_rule || overdueDays > 0) && (
        <div className="event-time">
          {card.scheduled_at && <><span className="clock-icon">&#128337;</span>{formatScheduled(card.scheduled_at)}</>}
          {card.recurrence_rule && (
            <span className="card-recurrence">&#8635; {card.recurrence_rule}</span>
          )}
          {overdueDays > 0 && (
            <span className="card-overdue-badge">
              &#9888; {overdueDays === 1 ? '1 day overdue' : `${overdueDays} days overdue`}
            </span>
          )}
        </div>
      )}
      {card.waiting_reason && (
        <div className="card-waiting-badge">
          &#9203; Waiting: {card.waiting_reason}
        </div>
      )}

      {expanded && !isOverlay && (
        <div className="event-details">
          {card.description && (() => {
            const gh = parseGitHubUrl(card.description)
            return gh ? (
              <a href={gh.url} target="_blank" rel="noopener noreferrer" className="card-github-badge" onClick={(e) => e.stopPropagation()}>
                {gh.repo} #{gh.number} ↗
              </a>
            ) : (
              <div className="event-detail-value">{card.description}</div>
            )
          })()}
          <div className="card-move-row">
            <span className="event-detail-label">Move to</span>
            <div className="card-move-buttons">
              {SECTIONS.filter((s) => s !== card.section).map((s) => (
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

      {(card.tags ?? []).length > 0 && (
        <div className="event-tags">
          {(card.tags ?? []).map((tag) => (
            <span key={tag.id} className="event-tag-pill" style={{ background: tag.color }}>
              {tag.name}
            </span>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={showConfirm}
        title="Delete card?"
        description={`"${card.title}" will be permanently deleted.`}
        onConfirm={() => { setShowConfirm(false); onDelete?.(card.id) }}
        onCancel={() => setShowConfirm(false)}
      />

      <AssistModal
        open={showAssist}
        onClose={() => setShowAssist(false)}
        task={card}
        onBreakdown={onBreakdown}
      />

      {showSheet && (
        <CardSheet
          card={card}
          allTags={allTags ?? []}
          onClose={() => setShowSheet(false)}
          onSave={onSave}
          onDelete={onDelete}
          onArchive={onArchive}
          onToggle={onToggle}
          onMove={onMove}
          onBreakdown={onBreakdown}
        />
      )}
    </div>
  )
}
