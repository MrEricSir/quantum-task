import { useState, useEffect } from 'react'
import { DndContext } from '@dnd-kit/core'
import { CheckIcon, ChevronDownIcon, ChevronUpIcon } from '@radix-ui/react-icons'
import Card from '../board/Card'
import CalendarEventCard from '../board/CalendarEventCard'
import DailyBriefing from '../shared/DailyBriefing'
import InsightsPanel from '../shared/InsightsPanel'
import { CollapseBody } from '../layout/Collapsible'
import './TodayPage.css'

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

function formatTodayDate() {
  const d = new Date()
  return `${DAY_NAMES[d.getDay()]}, ${MONTH_NAMES[d.getMonth()]} ${d.getDate()}`
}

function SectionHeader({ title, badge, status, open, onToggle, toggleable = false }) {
  return (
    <div
      className={`today-section-header${toggleable ? ' today-section-header--toggleable' : ''}`}
      onClick={toggleable ? onToggle : undefined}
      role={toggleable ? 'button' : undefined}
      tabIndex={toggleable ? 0 : undefined}
      onKeyDown={toggleable ? (e) => e.key === 'Enter' && onToggle() : undefined}
    >
      <span className="today-section-title-text">
        {title}
        {badge && <span className="today-section-badge">{badge}</span>}
      </span>
      <span className="today-section-status">{status}</span>
      {toggleable && (
        <span className="today-section-chevron">
          {open ? <ChevronUpIcon /> : <ChevronDownIcon />}
        </span>
      )}
    </div>
  )
}

const KG_TO_LBS = 2.20462

function MetricProgress({ habit, todayMetrics, isImperial }) {
  const metric = habit.health_metric
  const goal = habit.health_goal
  if (!metric || !todayMetrics) return null
  const value = todayMetrics[metric]
  if (value == null) return null

  if (metric === 'steps' && goal != null) {
    const pct = Math.min(100, Math.round((value / goal) * 100))
    return (
      <span className="today-habit-metric">
        <span className="today-habit-metric-text">
          {Math.round(value).toLocaleString()} / {Math.round(goal).toLocaleString()}
        </span>
        <span className="today-habit-metric-bar">
          <span className="today-habit-metric-fill" style={{ width: `${pct}%` }} />
        </span>
      </span>
    )
  }

  if (metric === 'fat_ratio') {
    const label = goal != null ? `${value.toFixed(1)}% / ≤${goal.toFixed(1)}%` : `${value.toFixed(1)}%`
    return <span className="today-habit-metric today-habit-metric--text">{label}</span>
  }

  if (metric === 'weight') {
    const toDisp = (kg) => isImperial ? Math.round(kg * KG_TO_LBS * 10) / 10 : kg
    const unit = isImperial ? 'lbs' : 'kg'
    const label = goal != null
      ? `${toDisp(value).toFixed(1)} ${unit} / ≤${toDisp(goal).toFixed(1)} ${unit}`
      : `${toDisp(value).toFixed(1)} ${unit}`
    return <span className="today-habit-metric today-habit-metric--text">{label}</span>
  }

  return null
}

export default function TodayPage({ cards, calendarEvents, habits, onToggle, onToggleHabit, onEdit, onSave, onDelete, onArchive, onMove, onWeather, briefingKey = 0, calendarReady = true, healthData, isImperial = false, allTags = [], onBreakdown, onExtractActions, onSelect, selectedCardId, bridgeJobStatuses, briefingAutoShow = false }) {
  const activeCards = cards.filter((t) => !t.completed)
  const overdueCards = activeCards.filter((t) => t.section !== 'today' && (t.overdue_days ?? 0) > 0)
  const todayCards   = activeCards.filter((t) => t.section === 'today')
  const allRelevant  = [...overdueCards, ...todayCards]

  // Timed tasks go in Schedule with events; untimed tasks appended below in Schedule
  const timedTasks   = allRelevant.filter((t) => t.scheduled_at)
  const untimedTasks = allRelevant.filter((t) => !t.scheduled_at)

  // Group overdue tasks together at top of schedule section
  const overdueTimedTasks   = timedTasks.filter((t) => (t.overdue_days ?? 0) > 0)
  const normalTimedTasks    = timedTasks.filter((t) => (t.overdue_days ?? 0) <= 0)
  const overdueUntimedTasks = untimedTasks.filter((t) => (t.overdue_days ?? 0) > 0)
  const normalUntimedTasks  = untimedTasks.filter((t) => (t.overdue_days ?? 0) <= 0)

  // All overdue tasks combined, sorted by most overdue first
  const allOverdueTasks = [...overdueTimedTasks, ...overdueUntimedTasks].sort(
    (a, b) => (b.overdue_days ?? 0) - (a.overdue_days ?? 0)
  )

  const sortedUntimedTasks = normalUntimedTasks.slice().sort(
    (a, b) => (a.position ?? 0) - (b.position ?? 0)
  )

  // The single task to visually emphasize as "up next" within the list below —
  // first overdue (untimed; a timed task already has its own schedule slot),
  // else the first untimed task in position order. Rendered inline via Card's
  // isFocus prop rather than duplicated in a separate callout.
  const focusCandidate = overdueUntimedTasks.length > 0
    ? overdueUntimedTasks.slice().sort((a, b) => (b.overdue_days ?? 0) - (a.overdue_days ?? 0))[0]
    : sortedUntimedTasks[0]
  const focusTaskId = focusCandidate?.id ?? null

  const today = new Date()
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  const todayEvents = calendarEvents.filter((e) => {
    // All-day events have a date-only start string; parse directly to avoid JS
    // treating "YYYY-MM-DD" as UTC midnight (which shifts the date in US timezones).
    if (e.all_day) return e.start.slice(0, 10) === todayKey
    const d = new Date(e.start)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` === todayKey
  })

  // Merge non-overdue timed tasks + calendar events, sort chronologically
  const scheduleItems = [
    ...todayEvents.map((e) => ({ type: 'event', data: e, time: e.all_day ? null : new Date(e.start) })),
    ...normalTimedTasks.map((t) => ({ type: 'task', data: t, time: new Date(t.scheduled_at) })),
  ].sort((a, b) => {
    if (!a.time && !b.time) return 0
    if (!a.time) return -1
    if (!b.time) return 1
    return a.time - b.time
  })

  const hasScheduleOrTasks = allOverdueTasks.length > 0 || scheduleItems.length > 0 || sortedUntimedTasks.length > 0

  // Build a map of metric → today's value from healthData, for habits linked to
  // a Withings metric (e.g. step-goal auto-completion progress)
  const todayMetrics = (() => {
    const measurements = healthData?.measurements ?? []
    const result = {}
    for (const m of measurements) {
      if (m.date === todayKey) result[m.metric] = m.value
    }
    return result
  })()

  const habitsDone    = habits.filter((h) => h.completed_today).length
  const habitsPending = habits.length - habitsDone
  const habitsAllDone = habits.length > 0 && habitsPending === 0

  const catchUpCount  = cards.filter((t) => !t.completed && t.section === 'week').length

  const allClear = scheduleItems.length === 0 && allOverdueTasks.length === 0 && untimedTasks.length === 0 && habitsPending === 0

  const [habitsOpen, setHabitsOpen] = useState(!habitsAllDone)

  useEffect(() => { if (habitsAllDone) setHabitsOpen(false) }, [habitsAllDone])

  const scheduleStatus = (() => {
    const evCount   = todayEvents.length
    const taskCount = timedTasks.length + untimedTasks.length
    const overdueCount = allOverdueTasks.length
    if (!evCount && !taskCount && !overdueCount) return ''
    const parts = []
    if (overdueCount) parts.push(`${overdueCount} overdue`)
    if (evCount)   parts.push(`${evCount} event${evCount !== 1 ? 's' : ''}`)
    if (taskCount) parts.push(`${taskCount} task${taskCount !== 1 ? 's' : ''}`)
    return parts.join(' · ')
  })()

  return (
    <DndContext sensors={[]}>  {/* no drag on Today — read-only card layout */}
      <div className="today-page">
        <div className="today-header">
          <h2 className="today-date">{formatTodayDate()}</h2>
          <div className="today-summary">
            {allClear ? (
              <span className="today-summary-clear">All clear</span>
            ) : (
              <>
                {timedTasks.length + todayEvents.length > 0 && (
                  <span>{timedTasks.length + todayEvents.length} scheduled</span>
                )}
                {untimedTasks.length > 0 && (
                  <span>{untimedTasks.length} task{untimedTasks.length !== 1 ? 's' : ''}</span>
                )}
                {habitsPending > 0 && (
                  <span>{habitsPending} habit{habitsPending !== 1 ? 's' : ''} pending</span>
                )}
              </>
            )}
          </div>
        </div>

        <DailyBriefing
          ready={calendarReady}
          todayOnly
          onWeather={onWeather}
          invalidationKey={briefingKey}
          collapsedByDefault={!briefingAutoShow}
        />

        <InsightsPanel
          refreshKey={briefingKey}
          onArchive={onArchive}
          cards={cards}
          habits={habits}
        />

        {habits.length > 0 && (
          <section className="today-section">
            <SectionHeader
              title="Habits"
              status={habitsAllDone ? 'All done' : `${habitsDone}/${habits.length}`}
              open={habitsOpen}
              onToggle={() => setHabitsOpen((v) => !v)}
              toggleable={habitsAllDone}
            />
            <CollapseBody open={habitsOpen}>
              <div className="today-habits">
                {habits.map((habit) => (
                  <div
                    key={habit.id}
                    className={`today-habit${habit.completed_today ? ' today-habit--done' : ''}`}
                  >
                    {(() => {
                      const isAuto = !!habit.health_metric || !!habit.food_avoid_name
                      const autoTitle = habit.food_avoid_name
                        ? 'Tracked automatically from your food log'
                        : 'Synced automatically from Withings'
                      return (
                        <button
                          type="button"
                          className={`today-habit-check${isAuto && !habit.completed_today ? ' today-habit-check--auto' : ''}`}
                          onClick={isAuto ? undefined : () => onToggleHabit(habit)}
                          disabled={isAuto}
                          title={isAuto ? autoTitle : undefined}
                          aria-label={habit.completed_today ? 'Mark incomplete' : 'Mark complete'}
                        >
                          {habit.completed_today
                            ? <CheckIcon width={11} height={11} />
                            : isAuto
                              ? <span className="habit-auto-icon">↻</span>
                              : null}
                        </button>
                      )
                    })()}
                    <span className="today-habit-name">{habit.name}</span>
                    <MetricProgress habit={habit} todayMetrics={todayMetrics} isImperial={isImperial} />
                    {habit.streak > 0 && (
                      <span className="today-habit-streak">{habit.streak}</span>
                    )}
                  </div>
                ))}
              </div>
            </CollapseBody>
          </section>
        )}

        {hasScheduleOrTasks && (
          <section className="today-section">
            <SectionHeader
              title="Schedule"
              status={scheduleStatus}
              open
              toggleable={false}
            />
            <div className="today-cards">
              {allOverdueTasks.length > 0 && (
                <>
                  <div className="today-group-label today-group-label--overdue">
                    ⚠ Overdue
                  </div>
                  {allOverdueTasks.map((todo) => (
                    <Card
                      key={todo.id}
                      card={todo}
                      onEdit={onEdit}
                      onSave={onSave}
                      onDelete={onDelete}
                      onArchive={onArchive}
                      onToggle={onToggle}
                      onMove={onMove}
                      allTags={allTags}
                      onBreakdown={onBreakdown}
                      onExtractActions={onExtractActions}
                      onSelect={onSelect}
                      isSelected={selectedCardId === todo.id}
                      inOverdueGroup
                      isFocus={todo.id === focusTaskId}
                      bridgeJobStatus={bridgeJobStatuses?.[todo.id]}
                    />
                  ))}
                  {(scheduleItems.length > 0 || sortedUntimedTasks.length > 0) && (
                    <div className="today-group-divider" />
                  )}
                </>
              )}
              {scheduleItems.map((item) =>
                item.type === 'event' ? (
                  <CalendarEventCard key={`ev-${item.data.id}`} event={item.data} />
                ) : (
                  <Card
                    key={`task-${item.data.id}`}
                    card={item.data}
                    onEdit={onEdit}
                    onSave={onSave}
                    onDelete={onDelete}
                    onArchive={onArchive}
                    onToggle={onToggle}
                    onMove={onMove}
                    allTags={allTags}
                    onBreakdown={onBreakdown}
                    onExtractActions={onExtractActions}
                    onSelect={onSelect}
                    isSelected={selectedCardId === item.data.id}
                    isFocus={item.data.id === focusTaskId}
                    bridgeJobStatus={bridgeJobStatuses?.[item.data.id]}
                  />
                )
              )}
              {sortedUntimedTasks.map((todo) => (
                <Card
                  key={todo.id}
                  card={todo}
                  onEdit={onEdit}
                  onSave={onSave}
                  onDelete={onDelete}
                  onArchive={onArchive}
                  onToggle={onToggle}
                  onMove={onMove}
                  allTags={allTags}
                  onBreakdown={onBreakdown}
                  onExtractActions={onExtractActions}
                  onSelect={onSelect}
                  isSelected={selectedCardId === todo.id}
                  isFocus={todo.id === focusTaskId}
                  bridgeJobStatus={bridgeJobStatuses?.[todo.id]}
                />
              ))}
            </div>
            {catchUpCount > 0 && (
              <div className="today-catchup">
                {catchUpCount} more task{catchUpCount !== 1 ? 's' : ''} in This Week
              </div>
            )}
          </section>
        )}

      </div>
    </DndContext>
  )
}
