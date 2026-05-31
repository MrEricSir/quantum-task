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
        {SECTIONS.map((section) => (
          <div key={section} className={`cal-strip-col ${activeSection !== null && section !== activeSection ? 'cal-strip-col--inactive' : ''}`}>
            {section === 'later' ? (
              <div className="cal-strip-col-empty cal-strip-col-empty--later">
                {bySection['month'].length > 0
                  ? `${bySection['month'].length} event${bySection['month'].length !== 1 ? 's' : ''} this month`
                  : 'Nothing upcoming'}
              </div>
            ) : bySection[section].length === 0 ? (
              <div className="cal-strip-col-empty">No events</div>
            ) : (
              bySection[section].map((event) => (
                <CalendarEventCard key={event.id} event={event} />
              ))
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
