import { useState, useEffect, useRef } from 'react'
import { CalendarIcon, ChevronUpIcon, ChevronDownIcon } from '@radix-ui/react-icons'
import descriptionToHtml from '../../lib/descriptionToHtml'
import './EventCard.css'
import './CalendarEventCard.css'

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

function formatDateRange(start, end, allDay) {
  if (allDay) return 'All day'
  const startTime = formatTime(start)
  if (!end) return startTime
  return `${startTime} – ${formatTime(end)}`
}

export default function CalendarEventCard({ event, highlighted = false, onHighlightClear }) {
  const [expanded, setExpanded] = useState(highlighted)
  const [flash, setFlash] = useState(highlighted)
  const cardRef = useRef(null)
  const borderColor = event.tag_color ?? '#6b7280'

  useEffect(() => {
    if (!highlighted) return
    cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const t = setTimeout(() => { setFlash(false); onHighlightClear?.() }, 1800)
    return () => clearTimeout(t)
  }, [highlighted]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={cardRef}
      className={`event-card${expanded ? ' event-card--expanded' : ''}${flash ? ' event-card--highlight' : ''}`}
      style={{ borderLeftColor: borderColor }}
      onClick={() => setExpanded((v) => !v)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && setExpanded((v) => !v)}
    >
      <div className="event-header">
        <span className="event-icon"><CalendarIcon /></span>
        <span className="event-title">{event.title}</span>
        {event.url && (
          <a
            href={event.url}
            target="_blank"
            rel="noopener noreferrer"
            className="event-open-btn"
            onClick={(e) => e.stopPropagation()}
            title="Open in calendar"
          >
            ↗
          </a>
        )}
        <span className="event-chevron">{expanded ? <ChevronUpIcon /> : <ChevronDownIcon />}</span>
      </div>

      <div className="event-time">
        {formatDateRange(event.start, event.end, event.all_day)}
      </div>

      {expanded && (
        <div className="event-details">
          <div className="event-detail-row">
            <span className="event-detail-label">Date</span>
            <span className="event-detail-value">{formatDate(event.start)}</span>
          </div>
          {!event.all_day && event.end && (
            <div className="event-detail-row">
              <span className="event-detail-label">Time</span>
              <span className="event-detail-value">{formatTime(event.start)} – {formatTime(event.end)}</span>
            </div>
          )}
          {event.location && (
            <div className="event-detail-row">
              <span className="event-detail-label">Location</span>
              <a
                className="event-detail-value event-location-link"
                href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(event.location)}`}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                {event.location}
              </a>
            </div>
          )}
          {event.description && (
            <div className="event-detail-row">
              <span className="event-detail-label">Notes</span>
              <span
                className="cal-event-description"
                dangerouslySetInnerHTML={{ __html: descriptionToHtml(event.description) }}
              />
            </div>
          )}
        </div>
      )}

      {event.tag_name && (
        <div className="event-tags">
          <span className="event-tag-pill" style={{ background: event.tag_color ?? '#6b7280' }}>
            {event.tag_name}
          </span>
        </div>
      )}
    </div>
  )
}
