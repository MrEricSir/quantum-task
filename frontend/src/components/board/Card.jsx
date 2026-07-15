import { useState, useEffect, useRef, Fragment } from 'react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import * as Checkbox from '@radix-ui/react-checkbox'
import { CheckIcon } from '@radix-ui/react-icons'
import ConfirmDialog from '../modals/ConfirmDialog'
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

export default function Card({ card, onEdit, onSave, onDelete, onArchive, onToggle, onMove, isMobile, isOverlay, allTags, onBreakdown, onSelect, isSelected, inOverdueGroup = false }) {
  const [showSheet,   setShowSheet]   = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [popping,     setPopping]     = useState(false)
  const popTimer = useRef(null)

  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: card.id, disabled: !!isOverlay || !!isMobile })

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

  const handleCardClick = () => {
    if (isOverlay) return
    if (window.matchMedia('(max-width: 640px)').matches) setShowSheet(true)
    else onSelect?.(card)
  }

  // Carryover: incomplete today-cards that entered 'today' before the current local day
  const carryoverDays = (() => {
    if (isOverlay || card.section !== 'today' || !card.today_since || card.completed || overdueDays > 0) return 0
    const now = new Date()
    const localToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    return Math.max(0, Math.floor((localToday.getTime() - new Date(card.today_since).getTime()) / 86400000))
  })()

  const metaParts = []
  if (card.scheduled_at) metaParts.push({ key: 'time', cls: null, text: formatScheduled(card.scheduled_at) })
  if (card.recurrence_rule) metaParts.push({ key: 'rec', cls: 'card-recurrence', text: card.recurrence_rule })
  if (overdueDays > 0 && !inOverdueGroup) metaParts.push({ key: 'overdue', cls: 'card-meta-overdue', text: `${overdueDays}d overdue` })
  if (carryoverDays > 0) metaParts.push({ key: 'carryover', cls: 'card-meta-carryover', text: `${carryoverDays}d`, title: `In Today for ${carryoverDays} day${carryoverDays === 1 ? '' : 's'}` })
  if (card.waiting_reason) metaParts.push({ key: 'wait', cls: 'card-meta-waiting', text: `Waiting: ${card.waiting_reason}` })

  return (
    <div
      ref={isOverlay ? undefined : setNodeRef}
      style={style}
      className={[
        'event-card',
        card.completed  ? 'event-card--done'    : '',
        overdueDays > 0 ? 'event-card--overdue' : '',
        isDragging      ? 'event-card--dragging' : '',
        isOverlay       ? 'event-card--overlay'  : '',
        isSelected      ? 'event-card--selected' : '',
      ].filter(Boolean).join(' ')}
      onClick={handleCardClick}
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
        {card.thread_output && !isOverlay && (
          <span className="card-output-dot" title="Has assistant output">✦</span>
        )}
      </div>

      {metaParts.length > 0 && (
        <div className="event-time">
          {metaParts.map((p, i) => (
            <Fragment key={p.key}>
              {i > 0 && <span className="card-meta-sep">·</span>}
              <span className={p.cls || undefined} title={p.title}>{p.text}</span>
            </Fragment>
          ))}
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
