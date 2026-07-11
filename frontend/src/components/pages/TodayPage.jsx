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
  const metric = habit.withings_metric
  const goal = habit.withings_goal
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

// Mini sparkline for 30-day metric trends
function MiniSparkline({ values, goal }) {
  if (values.length < 2) return null
  const W = 80; const H = 24; const PX = 2; const PY = 3
  const iW = W - PX * 2; const iH = H - PY * 2
  const allV = goal != null ? [...values, goal] : values
  const minV = Math.min(...allV); const maxV = Math.max(...allV)
  const rng = maxV - minV || 1
  const sx = (i) => PX + (i / (values.length - 1)) * iW
  const sy = (v) => PY + iH - ((v - minV) / rng) * iH
  const pts = values.map((v, i) => `${sx(i)},${sy(v)}`).join(' ')
  const area = [`${sx(0)},${H}`, ...values.map((v, i) => `${sx(i)},${sy(v)}`), `${sx(values.length - 1)},${H}`].join(' ')
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ flexShrink: 0, display: 'block' }}>
      <polygon points={area} fill="rgba(139,92,246,0.1)" />
      <polyline points={pts} fill="none" stroke="rgba(139,92,246,0.55)" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      {goal != null && (
        <line x1={PX} x2={W - PX} y1={sy(goal)} y2={sy(goal)} stroke="#f59e0b" strokeWidth={1} strokeDasharray="3 2" />
      )}
    </svg>
  )
}

// Standalone metric row for metrics not tied to a habit — shows 30-day sparkline trend
function StandaloneMetricRow({ metric, goal, isImperial, measurements = [] }) {
  const toDisp = (kg) => isImperial ? Math.round(kg * KG_TO_LBS * 10) / 10 : kg
  const labels = { weight: 'Weight', fat_ratio: 'Body Fat' }
  const label = labels[metric] ?? metric
  const unit = metric === 'weight' ? (isImperial ? 'lbs' : 'kg') : '%'

  // Last 30 data points for this metric, converted to display units
  const history = measurements.filter(m => m.metric === metric).slice(-30)
  const dispValues = history.map(m => metric === 'weight' ? toDisp(m.value) : m.value)
  const dispGoal = goal != null ? (metric === 'weight' ? toDisp(goal) : goal) : null

  // Most recent reading and how old it is
  const recent = history.length > 0 ? history[history.length - 1] : null
  const recentDisp = recent != null ? (metric === 'weight' ? toDisp(recent.value) : recent.value) : null

  let dateStr = ''
  if (recent) {
    const now = new Date()
    const todayKey = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`
    const yest = new Date(now); yest.setDate(now.getDate()-1)
    const yestKey = `${yest.getFullYear()}-${String(yest.getMonth()+1).padStart(2,'0')}-${String(yest.getDate()).padStart(2,'0')}`
    if (recent.date !== todayKey) {
      dateStr = recent.date === yestKey ? 'yesterday'
        : new Date(recent.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    }
  }

  if (!recent && dispGoal == null) return null

  return (
    <div className="today-habit today-habit--standalone-metric">
      <span className="today-habit-name">{label}</span>
      <span className="today-standalone-value">
        {recentDisp != null && (
          <span className="today-standalone-reading">
            {recentDisp.toFixed(1)}{metric === 'fat_ratio' ? '%' : ` ${unit}`}
            {dateStr && <span className="today-standalone-date"> · {dateStr}</span>}
          </span>
        )}
        {dispGoal != null && (
          <span className="today-standalone-goal">goal ≤ {dispGoal.toFixed(1)}{metric === 'fat_ratio' ? '%' : ` ${unit}`}</span>
        )}
      </span>
      {dispValues.length >= 2 && <MiniSparkline values={dispValues} goal={dispGoal} />}
    </div>
  )
}

export default function TodayPage({ cards, calendarEvents, habits, onToggle, onToggleHabit, onEdit, onSave, onDelete, onArchive, onMove, onWeather, briefingKey = 0, calendarReady = true, healthData, healthGoals, isImperial = false, allTags = [], onBreakdown }) {
  const activeCards = cards.filter((t) => !t.completed)
  const overdueCards = activeCards.filter((t) => t.section !== 'today' && (t.overdue_days ?? 0) > 0)
  const todayCards   = activeCards.filter((t) => t.section === 'today')
  const allRelevant  = [...overdueCards, ...todayCards]

  // Timed tasks go in Schedule with events; untimed tasks appended below in Schedule
  const timedTasks   = allRelevant.filter((t) => t.scheduled_at)
  const untimedTasks = allRelevant.filter((t) => !t.scheduled_at)

  const sortedUntimedTasks = untimedTasks.slice().sort((a, b) => {
    const aOverdue = a.overdue_days ?? 0
    const bOverdue = b.overdue_days ?? 0
    if (aOverdue !== bOverdue) return bOverdue - aOverdue
    return (a.position ?? 0) - (b.position ?? 0)
  })

  const today = new Date()
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  const todayEvents = calendarEvents.filter((e) => {
    // All-day events have a date-only start string; parse directly to avoid JS
    // treating "YYYY-MM-DD" as UTC midnight (which shifts the date in US timezones).
    if (e.all_day) return e.start.slice(0, 10) === todayKey
    const d = new Date(e.start)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` === todayKey
  })

  // Merge timed tasks + calendar events, sort chronologically
  const scheduleItems = [
    ...todayEvents.map((e) => ({ type: 'event', data: e, time: e.all_day ? null : new Date(e.start) })),
    ...timedTasks.map((t) => ({ type: 'task', data: t, time: new Date(t.scheduled_at) })),
  ].sort((a, b) => {
    if (!a.time && !b.time) return 0
    if (!a.time) return -1
    if (!b.time) return 1
    return a.time - b.time
  })

  const hasScheduleOrTasks = scheduleItems.length > 0 || sortedUntimedTasks.length > 0

  // Build a map of metric → today's value from healthData
  const todayMetrics = (() => {
    const measurements = healthData?.measurements ?? []
    const result = {}
    for (const m of measurements) {
      if (m.date === todayKey) result[m.metric] = m.value
    }
    return result
  })()

  // Standalone health metrics: weight/fat_ratio with any historical data or a goal, but no linked habit
  const allMeasurements = healthData?.measurements ?? []
  const linkedMetrics = new Set(habits.map(h => h.withings_metric).filter(Boolean))
  const standaloneMetrics = ['weight', 'fat_ratio'].filter(metric => {
    if (linkedMetrics.has(metric)) return false  // handled by a habit's MetricProgress
    return allMeasurements.some(m => m.metric === metric) || healthGoals?.[metric] != null
  })

  const hasHealthOrHabits = habits.length > 0 || standaloneMetrics.length > 0
  const sectionTitle = habits.length > 0 && standaloneMetrics.length > 0
    ? 'Health & Habits'
    : habits.length > 0 ? 'Habits' : 'Health'

  const habitsDone    = habits.filter((h) => h.completed_today).length
  const habitsPending = habits.length - habitsDone
  const habitsAllDone = habits.length > 0 && habitsPending === 0

  const catchUpCount  = cards.filter((t) => !t.completed && t.section === 'week').length

  const overdueScheduleCount = timedTasks.filter((t) => (t.overdue_days ?? 0) > 0).length
                             + untimedTasks.filter((t) => (t.overdue_days ?? 0) > 0).length

  const allClear = scheduleItems.length === 0 && untimedTasks.length === 0 && habitsPending === 0

  const [habitsOpen, setHabitsOpen] = useState(!habitsAllDone)

  useEffect(() => { if (habitsAllDone) setHabitsOpen(false) }, [habitsAllDone])

  const scheduleStatus = (() => {
    const evCount   = todayEvents.length
    const taskCount = timedTasks.length + untimedTasks.length
    if (!evCount && !taskCount) return ''
    const parts = []
    if (evCount)   parts.push(`${evCount} event${evCount !== 1 ? 's' : ''}`)
    if (taskCount) parts.push(`${taskCount} task${taskCount !== 1 ? 's' : ''}`)
    return parts.join(' · ')
  })()

  return (
    <DndContext>
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
          cards={activeCards}
          calendarEvents={todayEvents}
          habits={habits}
          ready={calendarReady}
          todayOnly
          onWeather={onWeather}
          invalidationKey={briefingKey}
        />

        {sortedUntimedTasks.length > 0 && (() => {
          const focusTask = sortedUntimedTasks[0]
          const projectTag = focusTask.tags?.find((t) => t.name.startsWith('Project: '))
          const projectName = projectTag ? projectTag.name.slice('Project: '.length) : null
          return (
            <div className="focus-next">
              <span className="focus-next-label">Focus next</span>
              <span className="focus-next-title">
                {projectName && <span className="focus-next-project">{projectName} ›</span>}
                {focusTask.title}
              </span>
              {(focusTask.overdue_days ?? 0) > 0 && (
                <span className="focus-next-overdue">
                  {focusTask.overdue_days}d overdue
                </span>
              )}
            </div>
          )
        })()}

        <InsightsPanel
          refreshKey={briefingKey}
          onArchive={onArchive}
        />

        {hasHealthOrHabits && (
          <section className="today-section">
            <SectionHeader
              title={sectionTitle}
              status={habits.length > 0 ? (habitsAllDone ? 'All done' : `${habitsDone}/${habits.length}`) : ''}
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
                      const isAuto = !!habit.withings_metric
                      return (
                        <button
                          type="button"
                          className={`today-habit-check${isAuto && !habit.completed_today ? ' today-habit-check--auto' : ''}`}
                          onClick={isAuto ? undefined : () => onToggleHabit(habit)}
                          disabled={isAuto}
                          title={isAuto ? 'Synced automatically from Withings' : undefined}
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
                {standaloneMetrics.map(metric => (
                  <StandaloneMetricRow
                    key={metric}
                    metric={metric}
                    goal={healthGoals?.[metric] ?? null}
                    isImperial={isImperial}
                    measurements={allMeasurements}
                  />
                ))}
              </div>
            </CollapseBody>
          </section>
        )}

        {hasScheduleOrTasks && (
          <section className="today-section">
            <SectionHeader
              title="Schedule"
              badge={overdueScheduleCount > 0 ? `${overdueScheduleCount} overdue` : null}
              status={scheduleStatus}
              open
              toggleable={false}
            />
            <div className="today-cards">
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
                    isMobile
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
                  isMobile
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
