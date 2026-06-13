import { CalendarIcon, UpdateIcon } from '@radix-ui/react-icons'
import CalendarEventCard from './CalendarEventCard'
import './CalendarStrip.css'

const SECTIONS = ['today', 'week', 'month', 'later']

function formatLastUpdated(date) {
  if (!date) return null
  const diffMin = Math.floor((Date.now() - date.getTime()) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin === 1) return '1 min ago'
  return `${diffMin} min ago`
}

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

// Group events by title; returns { singles, recurring } where recurring is an
// array of groups (each group = 2+ events with the same title).
function groupByTitle(events) {
  const map = {}
  for (const e of events) {
    if (!map[e.title]) map[e.title] = []
    map[e.title].push(e)
  }
  const singles = []
  const recurring = []
  for (const group of Object.values(map)) {
    if (group.length >= 2) recurring.push(group)
    else singles.push(group[0])
  }
  return { singles, recurring }
}

function RecurringEventCard({ events }) {
  const first = events[0]
  const borderColor = first.tag_color ?? '#6b7280'
  const days = events.map((e) =>
    new Date(e.start).toLocaleDateString(undefined, { weekday: 'short' })
  )
  const time = first.all_day ? 'All day' : formatTime(first.start)

  return (
    <div className="event-card recurring-event-card" style={{ borderLeftColor: borderColor }}>
      <div className="event-header">
        <span className="event-icon"><CalendarIcon /></span>
        <span className="event-title">{first.title}</span>
      </div>
      <div className="event-time">{time}</div>
      <div className="recurring-days">
        {days.map((d, i) => <span key={i} className="recurring-day-pill">{d}</span>)}
      </div>
      {first.tag_name && (
        <div className="event-tags">
          <span className="event-tag-pill" style={{ background: borderColor }}>{first.tag_name}</span>
        </div>
      )}
    </div>
  )
}

export default function CalendarStrip({ events, onRefresh, lastRefreshed, refreshing, activeSection }) {
  const bySection = SECTIONS.reduce((acc, s) => {
    acc[s] = events.filter((e) => e.section === s)
    return acc
  }, {})

  return (
    <div className="cal-strip">
      <div className="cal-strip-bar">
        <span className="cal-strip-title"><CalendarIcon /> Calendar</span>
        <div className="cal-strip-meta">
          {lastRefreshed && (
            <span className="cal-strip-updated">Updated {formatLastUpdated(lastRefreshed)}</span>
          )}
          <button
            className={`cal-strip-refresh ${refreshing ? 'cal-strip-refresh--spinning' : ''}`}
            onClick={onRefresh}
            disabled={refreshing}
            title="Refresh calendar events"
          >
            <UpdateIcon />
          </button>
        </div>
      </div>

      <div className="cal-strip-columns">
        {SECTIONS.map((section) => {
          const colClass = `cal-strip-col ${activeSection !== null && section !== activeSection ? 'cal-strip-col--inactive' : ''}`
          if (section === 'later') {
            return (
              <div key={section} className={colClass}>
                <div className="cal-strip-col-empty cal-strip-col-empty--later">
                  {bySection['month'].length > 0
                    ? `${bySection['month'].length} event${bySection['month'].length !== 1 ? 's' : ''} this month`
                    : 'Nothing upcoming'}
                </div>
              </div>
            )
          }
          if (section === 'week' || section === 'month') {
            const { singles, recurring } = groupByTitle(bySection[section])
            return (
              <div key={section} className={colClass}>
                {singles.length === 0 && recurring.length === 0 ? (
                  <div className="cal-strip-col-empty">No events</div>
                ) : (
                  <>
                    {recurring.map((group, i) => <RecurringEventCard key={i} events={group} />)}
                    {singles.map((event) => <CalendarEventCard key={event.id} event={event} />)}
                  </>
                )}
              </div>
            )
          }
          return (
            <div key={section} className={colClass}>
              {bySection[section].length === 0 ? (
                <div className="cal-strip-col-empty">No events</div>
              ) : (
                bySection[section].map((event) => (
                  <CalendarEventCard key={event.id} event={event} />
                ))
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
