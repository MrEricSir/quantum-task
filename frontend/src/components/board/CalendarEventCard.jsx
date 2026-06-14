import { useState } from 'react'
import DOMPurify from 'dompurify'
import { CalendarIcon, ChevronUpIcon, ChevronDownIcon } from '@radix-ui/react-icons'
import './EventCard.css'
import './CalendarEventCard.css'

// Make all links in sanitized HTML open safely in a new tab
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

const HTML_RE = /<[a-z][\s\S]*?>/i
const URL_RE = /(https?:\/\/[^\s<>"]+)/g

function descriptionToHtml(text) {
  if (!text) return ''
  if (HTML_RE.test(text)) {
    return DOMPurify.sanitize(text, { ADD_ATTR: ['target', 'rel'] })
  }
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
    .replace(URL_RE, (url) => {
      const safeHref = url.replace(/"/g, '%22')
      return `<a href="${safeHref}" target="_blank" rel="noopener noreferrer">${url}</a>`
    })
}

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

export default function CalendarEventCard({ event }) {
  const [expanded, setExpanded] = useState(false)
  const borderColor = event.tag_color ?? '#6b7280'

  return (
    <div
      className={`event-card ${expanded ? 'event-card--expanded' : ''}`}
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
