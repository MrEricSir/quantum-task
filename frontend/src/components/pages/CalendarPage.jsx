import { useState, useMemo, useEffect, useCallback } from 'react'
import { UpdateIcon, ChevronLeftIcon, ChevronRightIcon } from '@radix-ui/react-icons'
import CalendarEventCard from '../board/CalendarEventCard'
import { fetchDiscoveryEvents, fetchDiscoveryFeedback, saveDiscoveryFeedback, createCard } from '../../api'
import { useModalContext } from '../../context/ModalContext'
import descriptionToHtml from '../../lib/descriptionToHtml'
import './CalendarPage.css'

const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December']
const DAY_HEADERS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']

function formatLastUpdated(date) {
  if (!date) return null
  const diffMin = Math.floor((Date.now() - date.getTime()) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin === 1) return '1 min ago'
  return `${diffMin} min ago`
}

function toDateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

function sortItems(items) {
  return items.slice().sort((a, b) => {
    if (!a.time && !b.time) return 0
    if (!a.time) return -1
    if (!b.time) return 1
    return a.time - b.time
  })
}

function CalendarTaskRow({ todo, onToggle, onEdit }) {
  return (
    <div className="calp-task-row" onClick={() => onEdit(todo)} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onEdit(todo)}>
      <button
        type="button"
        className="calp-task-check"
        onClick={(e) => { e.stopPropagation(); onToggle(todo) }}
        aria-label="Toggle complete"
      />
      <span className="calp-task-time">{formatTime(todo.scheduled_at)}</span>
      <span className="calp-task-title">{todo.title}</span>
      <div className="calp-task-tags">
        {(todo.tags ?? []).map((tag) => (
          <span key={tag.id} className="calp-task-dot" style={{ background: tag.color }} title={tag.name} />
        ))}
      </div>
    </div>
  )
}

function buildDayMap(events, cards) {
  const map = {}
  for (const e of events) {
    const key = toDateKey(new Date(e.start))
    if (!map[key]) map[key] = { events: [], tasks: [] }
    map[key].events.push(e)
  }
  for (const t of cards) {
    if (!t.scheduled_at) continue
    const key = toDateKey(new Date(t.scheduled_at))
    if (!map[key]) map[key] = { events: [], tasks: [] }
    map[key].tasks.push(t)
  }
  return map
}

function getListDays() {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Array.from({ length: 28 }, (_, i) => {
    const d = new Date(today)
    d.setDate(today.getDate() + i)
    return d
  })
}

function buildMonthCells(year, month) {
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells = []
  for (let i = 0; i < firstDay; i++) {
    cells.push({ date: new Date(year, month, 1 - (firstDay - i)), outside: true })
  }
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push({ date: new Date(year, month, d), outside: false })
  }
  while (cells.length % 7 !== 0 || cells.length < 35) {
    const n = cells.length - firstDay - daysInMonth + 1
    cells.push({ date: new Date(year, month + 1, n), outside: true })
  }
  return cells
}

function formatDiscoveryDate(isoStr) {
  const d = new Date(isoStr)
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

function formatDiscoveryTime(isoStr, allDay) {
  if (allDay) return null
  return new Date(isoStr).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

function DiscoveryPanel({ refreshTrigger }) {
  const { openCalendarSettings } = useModalContext()
  const [events, setEvents] = useState(null)  // null = not yet loaded
  const [loading, setLoading] = useState(false)
  // feedback: { [event_uid]: true (liked) | false (disliked) | null (cleared) }
  const [feedback, setFeedback] = useState({})
  // UIDs that were already disliked when the view loaded — hidden from the list entirely
  const [initialDisliked, setInitialDisliked] = useState(new Set())
  // added: set of event ids added to calendar
  const [added, setAdded] = useState(new Set())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [data, fb] = await Promise.all([fetchDiscoveryEvents(), fetchDiscoveryFeedback()])
      const fbMap = {}
      const disliked = new Set()
      for (const r of fb) {
        fbMap[r.event_uid] = r.interested
        if (!r.interested) disliked.add(r.event_uid)
      }
      setInitialDisliked(disliked)
      // Strip events the user already disliked in a previous session
      setEvents(data.filter(ev => !disliked.has(ev.uid || ev.id)))
      setFeedback(fbMap)
    } catch {
      setEvents([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load, refreshTrigger])

  const handleFeedback = useCallback(async (ev, interested) => {
    const uid = ev.uid || ev.id
    // Toggle off if clicking the already-active button
    const current = feedback[uid]
    const next = current === interested ? null : interested
    setFeedback((prev) => ({ ...prev, [uid]: next }))
    try {
      if (next === null) {
        // No backend delete — just leave previous record; next ranking call will use latest
        return
      }
      await saveDiscoveryFeedback(uid, ev.title, ev.description || '', next)
    } catch {
      // revert on error
      setFeedback((prev) => ({ ...prev, [uid]: current }))
    }
  }, [feedback])

  const [expanded, setExpanded] = useState(new Set())
  const toggleExpanded = useCallback((id) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  const handleAddToCalendar = useCallback(async (ev) => {
    try {
      await createCard({
        title: ev.title,
        description: ev.url || ev.description || '',
        scheduled_at: ev.start,
        section: 'later',
      })
      setAdded((prev) => new Set([...prev, ev.id]))
    } catch {
      // silently ignore
    }
  }, [])

  const hasInterests = events?.some((e) => e.score != null)

  return (
    <>
      <div className="disc-view">
        {loading && events === null && (
          <div className="disc-loading-row">Loading…</div>
        )}

        {!loading && events !== null && events.length === 0 && (
          <div className="disc-empty">
            {hasInterests === false
              ? 'No upcoming events found in your discovery feeds.'
              : 'Add discovery feeds and describe your interests to get AI-ranked recommendations.'}
            <button className="disc-empty-settings" onClick={openCalendarSettings}>
              Open settings
            </button>
          </div>
        )}

        {events && events.length > 0 && (
          <div className="disc-event-list">
            {!hasInterests && (
              <p className="disc-no-interests-hint">
                Add a description of your interests in{' '}
                <button className="disc-inline-link" onClick={openCalendarSettings}>
                  settings
                </button>{' '}
                to get AI-ranked recommendations.
              </p>
            )}
                {events.map((ev) => {
                  const time = formatDiscoveryTime(ev.start, ev.all_day)
                  const endTime = ev.end ? formatDiscoveryTime(ev.end, ev.all_day) : null
                  const uid = ev.uid || ev.id
                  const liked = feedback[uid]
                  const isDismissed = liked === false
                  const isAdded = added.has(ev.id)
                  const isExpanded = expanded.has(ev.id)

                  if (isDismissed) return (
                    <div key={ev.id} className="disc-event-card disc-event-card--dismissed">
                      <span className="disc-dismissed-label">Dismissed</span>
                      <button className="disc-undo-btn" onClick={() => handleFeedback(ev, false)}>Undo</button>
                    </div>
                  )

                  return (
                    <div key={ev.id} className="disc-event-card">
                      <div className="disc-event-header">
                        {ev.score != null && (
                          <span className={`disc-score-badge disc-score-badge--${ev.score >= 8 ? 'high' : 'mid'}`}>
                            {ev.score >= 8 ? 'Great match' : 'Good match'}
                          </span>
                        )}
                        {ev.feed_name && (
                          <span className="disc-feed-badge">{ev.feed_name}</span>
                        )}
                        <div className="disc-event-actions">
                          <button
                            className={`disc-feedback-btn${liked === true ? ' disc-feedback-btn--active-like' : ''}`}
                            onClick={() => handleFeedback(ev, true)}
                            title="Interested"
                            aria-label="Mark as interested"
                          >👍</button>
                          <button
                            className={`disc-feedback-btn${liked === false ? ' disc-feedback-btn--active-dislike' : ''}`}
                            onClick={() => handleFeedback(ev, false)}
                            title="Not interested"
                            aria-label="Mark as not interested"
                          >👎</button>
                        </div>
                      </div>

                      <div
                        className="disc-event-body"
                        onClick={() => toggleExpanded(ev.id)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => e.key === 'Enter' && toggleExpanded(ev.id)}
                        aria-expanded={isExpanded}
                      >
                        <div className="disc-event-title">
                          <span>{ev.title}</span>
                          <span className={`disc-expand-chevron${isExpanded ? ' disc-expand-chevron--open' : ''}`}>›</span>
                        </div>
                        <div className="disc-event-meta">
                          <span>{formatDiscoveryDate(ev.start)}{time ? ` · ${time}` : ''}{endTime && endTime !== time ? `–${endTime}` : ''}</span>
                          {ev.location && <span> · {ev.location}</span>}
                        </div>
                        {ev.reason && !isExpanded && (
                          <div className="disc-event-reason">{ev.reason}</div>
                        )}
                      </div>

                      {isExpanded && (
                        <div className="disc-event-detail">
                          {ev.reason && (
                            <div className="disc-detail-reason">{ev.reason}</div>
                          )}
                          {ev.description && (
                            <div
                              className="disc-detail-description"
                              dangerouslySetInnerHTML={{ __html: descriptionToHtml(ev.description) }}
                            />
                          )}
                          {ev.location && (
                            <div className="disc-detail-row">
                              <span className="disc-detail-label">Location</span>
                              <span>{ev.location}</span>
                            </div>
                          )}
                          {ev.url && (
                            <a
                              href={ev.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="disc-detail-link"
                              onClick={(e) => e.stopPropagation()}
                            >
                              Visit event page →
                            </a>
                          )}
                        </div>
                      )}

                      {liked === true && (
                        <div className="disc-event-footer">
                          {isAdded
                            ? <span className="disc-added-confirm">Added to calendar ✓</span>
                            : <button className="disc-add-cal-btn" onClick={() => handleAddToCalendar(ev)}>
                                + Add to calendar
                              </button>
                          }
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
      </div>
    </>
  )
}

export default function CalendarPage({ events, cards, onToggle, onEdit, onRefresh, lastRefreshed, refreshing, highlightEventId, onHighlightClear }) {
  const todayDate = new Date()
  todayDate.setHours(0, 0, 0, 0)
  const todayKey = toDateKey(todayDate)

  const [view, setView] = useState('list')

  useEffect(() => {
    if (highlightEventId) setView('list')
  }, [highlightEventId])
  const [monthYear, setMonthYear] = useState({ year: todayDate.getFullYear(), month: todayDate.getMonth() })
  const [selectedDate, setSelectedDate] = useState(todayKey)
  const [discoveryRefreshTrigger, setDiscoveryRefreshTrigger] = useState(0)

  const activeCards = useMemo(() => cards.filter((t) => !t.completed && t.scheduled_at), [cards])
  const dayMap = useMemo(() => buildDayMap(events, activeCards), [events, activeCards])
  const listDays = useMemo(() => getListDays(), [])
  const monthCells = useMemo(() => buildMonthCells(monthYear.year, monthYear.month), [monthYear.year, monthYear.month])

  const selectedDayData = dayMap[selectedDate] ?? { events: [], tasks: [] }
  const selectedDayItems = sortItems([
    ...selectedDayData.events.map((e) => ({ type: 'event', data: e, time: e.all_day ? null : new Date(e.start) })),
    ...selectedDayData.tasks.map((t) => ({ type: 'task', data: t, time: new Date(t.scheduled_at) })),
  ])

  const prevMonth = () => setMonthYear(({ year, month }) =>
    month === 0 ? { year: year - 1, month: 11 } : { year, month: month - 1 }
  )
  const nextMonth = () => setMonthYear(({ year, month }) =>
    month === 11 ? { year: year + 1, month: 0 } : { year, month: month + 1 }
  )

  return (
    <div className="calp">
      <div className="calp-toolbar">
        <div className="calp-view-toggle">
          <button className={`calp-view-btn${view === 'list' ? ' calp-view-btn--active' : ''}`} onClick={() => setView('list')}>
            List
          </button>
          <button className={`calp-view-btn${view === 'month' ? ' calp-view-btn--active' : ''}`} onClick={() => setView('month')}>
            Month
          </button>
          <button className={`calp-view-btn${view === 'discover' ? ' calp-view-btn--active' : ''}`} onClick={() => setView('discover')}>
            Discover
          </button>
        </div>
        <div className="calp-meta">
          {view === 'discover' ? (
            <button
              className="calp-refresh"
              onClick={() => setDiscoveryRefreshTrigger((n) => n + 1)}
              title="Refresh discovery"
              aria-label="Refresh discovery events"
            >
              <UpdateIcon />
            </button>
          ) : (
            <>
              {lastRefreshed && (
                <span className="calp-updated">Updated {formatLastUpdated(lastRefreshed)}</span>
              )}
              <button
                className={`calp-refresh${refreshing ? ' calp-refresh--spinning' : ''}`}
                onClick={onRefresh}
                disabled={refreshing}
                title="Refresh calendar"
              >
                <UpdateIcon />
              </button>
            </>
          )}
        </div>
      </div>

      {view === 'list' ? (
        <div className="calp-list">
          {listDays.map((date) => {
            const key = toDateKey(date)
            const data = dayMap[key] ?? { events: [], tasks: [] }
            const isToday = key === todayKey
            if (!isToday && data.events.length === 0 && data.tasks.length === 0) return null
            const items = sortItems([
              ...data.events.map((e) => ({ type: 'event', data: e, time: e.all_day ? null : new Date(e.start) })),
              ...data.tasks.map((t) => ({ type: 'task', data: t, time: new Date(t.scheduled_at) })),
            ])
            return (
              <div key={key} className={`calp-day-group${isToday ? ' calp-day-group--today' : ''}`}>
                <div className="calp-day-heading">
                  <span className="calp-day-name">
                    {date.toLocaleDateString(undefined, { weekday: 'short' })}
                  </span>
                  <span className="calp-day-date">
                    {date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                  </span>
                  {isToday && <span className="calp-today-badge">Today</span>}
                </div>
                <div className="calp-day-items">
                  {items.length === 0 ? (
                    <div className="calp-day-empty">No events</div>
                  ) : (
                    items.map((item) =>
                      item.type === 'event' ? (
                        <CalendarEventCard
                          key={`ev-${item.data.id}`}
                          event={item.data}
                          highlighted={item.data.id === highlightEventId}
                          onHighlightClear={onHighlightClear}
                        />
                      ) : (
                        <CalendarTaskRow key={`task-${item.data.id}`} todo={item.data} onToggle={onToggle} onEdit={onEdit} />
                      )
                    )
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ) : view === 'month' ? (
        <div className="calp-month-view">
          <div className="calp-month-nav">
            <button className="calp-month-nav-btn" onClick={prevMonth} aria-label="Previous month">
              <ChevronLeftIcon />
            </button>
            <span className="calp-month-title">{MONTH_NAMES[monthYear.month]} {monthYear.year}</span>
            <button className="calp-month-nav-btn" onClick={nextMonth} aria-label="Next month">
              <ChevronRightIcon />
            </button>
          </div>

          <div className="calp-grid">
            {DAY_HEADERS.map((d) => (
              <div key={d} className="calp-grid-dow">{d}</div>
            ))}
            {monthCells.map(({ date, outside }) => {
              const key = toDateKey(date)
              const isToday = key === todayKey
              const isSelected = key === selectedDate
              const data = dayMap[key] ?? { events: [], tasks: [] }
              const allLabels = [
                ...data.events.map((e) => ({ color: e.tag_color ?? '#6b7280', title: e.title })),
                ...data.tasks.map((t) => ({ color: (t.tags ?? [])[0]?.color ?? '#6b7280', title: t.title })),
              ]
              const visibleLabels = allLabels.slice(0, 2)
              const extraCount = allLabels.length - visibleLabels.length
              return (
                <div
                  key={key + (outside ? '-out' : '')}
                  className={`calp-grid-cell${outside ? ' calp-grid-cell--outside' : ''}${isToday ? ' calp-grid-cell--today' : ''}${isSelected ? ' calp-grid-cell--selected' : ''}`}
                  onClick={() => setSelectedDate(key)}
                  role="button"
                  tabIndex={outside ? -1 : 0}
                  onKeyDown={(e) => e.key === 'Enter' && setSelectedDate(key)}
                >
                  <span className="calp-grid-day">{date.getDate()}</span>
                  <div className="calp-grid-labels">
                    {visibleLabels.map((label, i) => (
                      <div key={i} className="calp-grid-label">
                        <span className="calp-grid-label-pip" style={{ background: label.color }} />
                        <span className="calp-grid-label-text">{label.title}</span>
                      </div>
                    ))}
                    {extraCount > 0 && (
                      <div className="calp-grid-more">+{extraCount} more</div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          <div className="calp-day-panel">
            <div className="calp-day-panel-header">
              <span className="calp-day-panel-date">
                {new Date(selectedDate + 'T00:00:00').toLocaleDateString(undefined, {
                  weekday: 'long', month: 'long', day: 'numeric',
                })}
              </span>
              {selectedDate === todayKey && <span className="calp-today-badge">Today</span>}
            </div>
            {selectedDayItems.length === 0 ? (
              <div className="calp-day-panel-empty">No events or tasks</div>
            ) : (
              <div className="calp-day-panel-items">
                {selectedDayItems.map((item) =>
                  item.type === 'event' ? (
                    <CalendarEventCard key={`ev-${item.data.id}`} event={item.data} />
                  ) : (
                    <CalendarTaskRow key={`task-${item.data.id}`} todo={item.data} onToggle={onToggle} onEdit={onEdit} />
                  )
                )}
              </div>
            )}
          </div>
        </div>
      ) : null}

      {view === 'discover' && (
        <DiscoveryPanel
          refreshTrigger={discoveryRefreshTrigger}
        />
      )}
    </div>
  )
}
