import { CalendarIcon, LoopIcon, SunIcon, TableIcon, CommitIcon, LightningBoltIcon } from '@radix-ui/react-icons'
import './Sidebar.css'

const NAV_ITEMS = [
  { page: 'today',       label: 'Today',       Icon: SunIcon           },
  { page: 'board',       label: 'Board',       Icon: TableIcon         },
  { page: 'calendar',    label: 'Calendar',    Icon: CalendarIcon      },
  { page: 'habits',      label: 'Habits',      Icon: LoopIcon          },
  { page: 'engineering', label: 'Engineering', Icon: CommitIcon        },
  { page: 'workshop',    label: 'Workshop',    Icon: LightningBoltIcon },
]

function localDateKey(event) {
  if (event.all_day) return event.start.slice(0, 10)
  const d = new Date(event.start)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function todayKey() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatEventTime(event) {
  const today = todayKey()
  const eventDay = localDateKey(event)
  const isToday = eventDay === today

  if (event.all_day) {
    const label = isToday ? 'Today' : new Date(eventDay + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
    return `${label} · All day`
  }

  const d = new Date(event.start)
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  if (isToday) return `Today · ${time}`
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) + ` · ${time}`
}

export default function Sidebar({ tags, selectedTagId, page, onNavigate, calendarEvents = [] }) {
  const showTags = tags.length > 0

  const today = todayKey()
  const upcomingEvents = calendarEvents
    .filter(e => localDateKey(e) >= today)
    .sort((a, b) => {
      const aMs = a.all_day ? new Date(a.start.slice(0, 10) + 'T00:00:00').getTime() : new Date(a.start).getTime()
      const bMs = b.all_day ? new Date(b.start.slice(0, 10) + 'T00:00:00').getTime() : new Date(b.start).getTime()
      return aMs - bMs
    })
    .slice(0, 8)

  return (
    <aside className="sidebar">
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ page: p, label, Icon }) => (
          <button
            key={p}
            className={`sidebar-item ${page === p ? 'sidebar-item--active' : ''}`}
            onClick={() => onNavigate(p, null)}
          >
            <span className="sidebar-item-icon"><Icon /></span>
            {label}
          </button>
        ))}
      </nav>

      {showTags && (
        <>
          <div className="sidebar-section-label">Tags</div>
          <nav className="sidebar-nav">
            {tags.map((tag) => (
              <button
                key={tag.id}
                className={`sidebar-item ${selectedTagId === tag.id ? 'sidebar-item--active' : ''}`}
                onClick={() => onNavigate(page, selectedTagId === tag.id ? null : tag.id)}
              >
                <span className="sidebar-dot" style={{ background: tag.color }} />
                {tag.name}
              </button>
            ))}
          </nav>
        </>
      )}

      <div className="sidebar-section-label">Upcoming</div>
      <div className="sidebar-upcoming">
        {upcomingEvents.length === 0 ? (
          <div className="sidebar-upcoming-empty">No upcoming events</div>
        ) : (
          upcomingEvents.map((event) => {
            const Tag = event.url ? 'a' : 'div'
            const linkProps = event.url
              ? { href: event.url, target: '_blank', rel: 'noopener noreferrer' }
              : {}
            return (
              <Tag key={event.id} className="sidebar-upcoming-event" {...linkProps}>
                <span
                  className="sidebar-upcoming-dot"
                  style={{ background: event.tag_color ?? 'var(--text-muted)' }}
                />
                <div className="sidebar-upcoming-body">
                  <span className="sidebar-upcoming-title">{event.title}</span>
                  <span className="sidebar-upcoming-time">{formatEventTime(event)}</span>
                </div>
              </Tag>
            )
          })
        )}
      </div>
    </aside>
  )
}
