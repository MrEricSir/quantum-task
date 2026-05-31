import { useState, useEffect } from 'react'
import { DndContext } from '@dnd-kit/core'
import { CheckIcon, ChevronDownIcon, ChevronUpIcon } from '@radix-ui/react-icons'
import TodoCard from './TodoCard'
import CalendarEventCard from './CalendarEventCard'
import DailyBriefing from './DailyBriefing'
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

export default function TodayPage({ todos, calendarEvents, habits, onToggle, onToggleHabit, onEdit, onDelete, onMove }) {
  const activeTodos = todos.filter((t) => !t.completed)
  const overdueTodos = activeTodos.filter((t) => t.section !== 'today' && (t.overdue_days ?? 0) > 0)
  const todayTodos   = activeTodos.filter((t) => t.section === 'today')
  const allRelevant  = [...overdueTodos, ...todayTodos]

  // Split into timed (goes in Schedule) and untimed (goes in Tasks)
  const timedTasks   = allRelevant.filter((t) => t.scheduled_at)
  const untimedTasks = allRelevant.filter((t) => !t.scheduled_at)

  // Sort untimed: overdue first, then by position
  const sortedUntimedTasks = untimedTasks.slice().sort((a, b) => {
    const aOverdue = a.overdue_days ?? 0
    const bOverdue = b.overdue_days ?? 0
    if (aOverdue !== bOverdue) return bOverdue - aOverdue
    return (a.position ?? 0) - (b.position ?? 0)
  })

  const todayEvents = calendarEvents.filter((e) => e.section === 'today')

  // Merge timed tasks + calendar events, sort chronologically
  // All-day events (no specific time) sort to top
  const scheduleItems = [
    ...todayEvents.map((e) => ({ type: 'event', data: e, time: e.all_day ? null : new Date(e.start) })),
    ...timedTasks.map((t) => ({ type: 'task', data: t, time: new Date(t.scheduled_at) })),
  ].sort((a, b) => {
    if (!a.time && !b.time) return 0
    if (!a.time) return -1
    if (!b.time) return 1
    return a.time - b.time
  })

  const hasSchedule = scheduleItems.length > 0

  const completedTodayCount = todos.filter((t) => t.completed && t.section === 'today').length
  const untimedAllDone = completedTodayCount > 0 && untimedTasks.length === 0 && timedTasks.length === 0

  const habitsDone    = habits.filter((h) => h.completed_today).length
  const habitsPending = habits.length - habitsDone
  const habitsAllDone = habits.length > 0 && habitsPending === 0

  const catchUpCount  = todos.filter((t) => !t.completed && t.section === 'week').length

  const overdueTimedCount   = timedTasks.filter((t) => (t.overdue_days ?? 0) > 0).length
  const overdueUntimedCount = untimedTasks.filter((t) => (t.overdue_days ?? 0) > 0).length

  const allClear = scheduleItems.length === 0 && untimedTasks.length === 0 && habitsPending === 0

  const [untimedOpen, setUuntimedOpen] = useState(!untimedAllDone)
  const [habitsOpen,  setHabitsOpen]   = useState(!habitsAllDone)

  useEffect(() => { if (habitsAllDone)  setHabitsOpen(false)  }, [habitsAllDone])
  useEffect(() => { if (untimedAllDone) setUuntimedOpen(false) }, [untimedAllDone])

  const scheduleStatus = (() => {
    const evCount   = todayEvents.length
    const taskCount = timedTasks.length
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
        />

        {hasSchedule && (
          <section className="today-section">
            <SectionHeader
              title="Schedule"
              badge={overdueTimedCount > 0 ? `${overdueTimedCount} overdue` : null}
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
            </div>
          </section>
        )}

        <section className="today-section">
          <SectionHeader
            title="Tasks"
            badge={overdueUntimedCount > 0 ? `${overdueUntimedCount} overdue` : null}
            status={
              untimedAllDone
                ? 'All done'
                : untimedTasks.length === 0
                  ? 'Nothing yet'
                  : `${untimedTasks.length} remaining`
            }
            open={untimedOpen}
            onToggle={() => setUuntimedOpen((v) => !v)}
            toggleable={untimedAllDone}
          />
          {untimedOpen && (
            sortedUntimedTasks.length === 0 ? (
              <div className="today-empty">
                {untimedAllDone ? 'All tasks complete.' : 'No unscheduled tasks for today.'}
              </div>
            ) : (
              <>
                <div className="today-cards">
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
              </>
            )
          )}
        </section>

        {habits.length > 0 && (
          <section className="today-section">
            <SectionHeader
              title="Habits"
              status={habitsAllDone ? 'All done' : `${habitsDone}/${habits.length}`}
              open={habitsOpen}
              onToggle={() => setHabitsOpen((v) => !v)}
              toggleable={habitsAllDone}
            />
            {habitsOpen && (
              <div className="today-habits">
                {habits.map((habit) => (
                  <div
                    key={habit.id}
                    className={`today-habit${habit.completed_today ? ' today-habit--done' : ''}`}
                  >
                    <button
                      type="button"
                      className="today-habit-check"
                      onClick={() => onToggleHabit(habit)}
                      aria-label={habit.completed_today ? 'Mark incomplete' : 'Mark complete'}
                    >
                      {habit.completed_today && <CheckIcon width={11} height={11} />}
                    </button>
                    <span className="today-habit-name">{habit.name}</span>
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
            )}
          </section>
        )}
      </div>
    </DndContext>
  )
}
