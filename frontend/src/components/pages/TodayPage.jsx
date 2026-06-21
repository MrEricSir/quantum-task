import { useState, useEffect } from 'react'
import { DndContext } from '@dnd-kit/core'
import { CheckIcon, ChevronDownIcon, ChevronUpIcon } from '@radix-ui/react-icons'
import TodoCard from '../board/TodoCard'
import CalendarEventCard from '../board/CalendarEventCard'
import DailyBriefing from '../shared/DailyBriefing'
import DailyPlan from '../shared/DailyPlan'
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

// Standalone metric row for metrics not tied to a habit (e.g. a weight goal without a weight habit)
function StandaloneMetricRow({ metric, value, goal, isImperial }) {
  const toDisp = (kg) => isImperial ? Math.round(kg * KG_TO_LBS * 10) / 10 : kg
  const labels = { weight: 'Weight', fat_ratio: 'Body Fat' }
  const label = labels[metric] ?? metric

  let display
  if (metric === 'fat_ratio') {
    if (value != null && goal != null) display = `${value.toFixed(1)}% / ≤${goal.toFixed(1)}%`
    else if (value != null) display = `${value.toFixed(1)}%`
    else display = `Goal: ≤${goal.toFixed(1)}%`
  } else if (metric === 'weight') {
    const unit = isImperial ? 'lbs' : 'kg'
    if (value != null && goal != null) display = `${toDisp(value).toFixed(1)} ${unit} / ≤${toDisp(goal).toFixed(1)} ${unit}`
    else if (value != null) display = `${toDisp(value).toFixed(1)} ${unit}`
    else display = `Goal: ≤${toDisp(goal).toFixed(1)} ${unit}`
  } else {
    display = value != null ? String(value) : `Goal: ${goal}`
  }

  return (
    <div className="today-habit today-habit--standalone-metric">
      <span className="today-habit-name">{label}</span>
      <span className="today-habit-metric today-habit-metric--text">{display}</span>
    </div>
  )
}

export default function TodayPage({ todos, calendarEvents, habits, onToggle, onToggleHabit, onEdit, onDelete, onMove, onWeather, briefingKey = 0, healthData, healthGoals, isImperial = false }) {
  const activeTodos = todos.filter((t) => !t.completed)
  const overdueTodos = activeTodos.filter((t) => t.section !== 'today' && (t.overdue_days ?? 0) > 0)
  const todayTodos   = activeTodos.filter((t) => t.section === 'today')
  const allRelevant  = [...overdueTodos, ...todayTodos]

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

  // Stash: section='later' or 'none', not completed, not archived
  const stashTodos = todos
    .filter((t) => (t.section === 'later' || t.section === 'none') && !t.completed && !t.archived)
    .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))

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

  // Standalone health metrics: weight/fat_ratio with a value or goal, but no linked habit
  const linkedMetrics = new Set(habits.map(h => h.withings_metric).filter(Boolean))
  const standaloneMetrics = ['weight', 'fat_ratio'].filter(metric => {
    if (linkedMetrics.has(metric)) return false  // handled by a habit's MetricProgress
    return todayMetrics[metric] != null || healthGoals?.[metric] != null
  })

  const habitsDone    = habits.filter((h) => h.completed_today).length
  const habitsPending = habits.length - habitsDone
  const habitsAllDone = habits.length > 0 && habitsPending === 0

  const catchUpCount  = todos.filter((t) => !t.completed && t.section === 'week').length

  const overdueScheduleCount = timedTasks.filter((t) => (t.overdue_days ?? 0) > 0).length
                             + untimedTasks.filter((t) => (t.overdue_days ?? 0) > 0).length

  const allClear = scheduleItems.length === 0 && untimedTasks.length === 0 && habitsPending === 0

  const [habitsOpen, setHabitsOpen] = useState(!habitsAllDone)
  const [stashOpen,  setStashOpen]  = useState(true)

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
          todos={activeTodos}
          calendarEvents={todayEvents}
          habits={habits}
          ready
          todayOnly
          onWeather={onWeather}
          invalidationKey={briefingKey}
        />

        <DailyPlan
          todos={activeTodos}
          calendarEvents={todayEvents}
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
                    <button
                      type="button"
                      className="today-habit-check"
                      onClick={habit.withings_metric ? undefined : () => onToggleHabit(habit)}
                      disabled={!!habit.withings_metric}
                      title={habit.withings_metric ? 'Auto-checked by health sync' : undefined}
                      aria-label={habit.completed_today ? 'Mark incomplete' : 'Mark complete'}
                    >
                      {habit.completed_today && <CheckIcon width={11} height={11} />}
                    </button>
                    <span className="today-habit-name">{habit.name}</span>
                    <MetricProgress habit={habit} todayMetrics={todayMetrics} isImperial={isImperial} />
                    {habit.tags.length > 0 && (
                      <div className="today-habit-dots">
                        {habit.tags.map((tag) => (
                          <span key={tag.id} className="today-habit-dot" style={{ background: tag.color }} title={tag.name} />
                        ))}
                      </div>
                    )}
                    {habit.streak > 0 && (
                      <span className="today-habit-streak">{habit.streak}</span>
                    )}
                  </div>
                ))}
              </div>
            </CollapseBody>
          </section>
        )}

        {standaloneMetrics.length > 0 && (
          <section className="today-section">
            <SectionHeader title="Health" open toggleable={false} />
            <div className="today-habits">
              {standaloneMetrics.map(metric => (
                <StandaloneMetricRow
                  key={metric}
                  metric={metric}
                  value={todayMetrics[metric] ?? null}
                  goal={healthGoals?.[metric] ?? null}
                  isImperial={isImperial}
                />
              ))}
            </div>
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
                  <TodoCard
                    key={`task-${item.data.id}`}
                    todo={item.data}
                    onEdit={onEdit}
                    onDelete={onDelete}
                    onToggle={onToggle}
                    onMove={onMove}
                    isMobile
                  />
                )
              )}
              {sortedUntimedTasks.map((todo) => (
                <TodoCard
                  key={todo.id}
                  todo={todo}
                  onEdit={onEdit}
                  onDelete={onDelete}
                  onToggle={onToggle}
                  onMove={onMove}
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

        <section className="today-section">
          <SectionHeader
            title="Stash"
            status={stashTodos.length === 0 ? 'Empty' : `${stashTodos.length}`}
            open={stashOpen}
            onToggle={() => setStashOpen((v) => !v)}
            toggleable
          />
          <CollapseBody open={stashOpen}>
            {stashTodos.length === 0 ? (
              <div className="today-empty">Nothing in your stash.</div>
            ) : (
              <div className="today-cards">
                {stashTodos.map((todo) => (
                  <TodoCard
                    key={todo.id}
                    todo={todo}
                    onEdit={onEdit}
                    onDelete={onDelete}
                    onToggle={onToggle}
                    onMove={onMove}
                    isMobile
                  />
                ))}
              </div>
            )}
          </CollapseBody>
        </section>
      </div>
    </DndContext>
  )
}
