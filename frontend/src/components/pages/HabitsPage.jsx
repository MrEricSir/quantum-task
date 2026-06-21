import { useState, useRef, useEffect } from 'react'
import { Pencil1Icon, CheckIcon, Cross2Icon } from '@radix-ui/react-icons'
import Collapsible from '../layout/Collapsible'
import './HabitsPage.css'

function HabitsArchive({ habits, onUnarchive, onDelete }) {
  if (habits.length === 0) return null
  return (
    <div className="habits-archive">
      <Collapsible label="Habit Archive" count={habits.length} defaultOpen={true}>
        <div className="habits-archive-list">
          {habits.map((habit) => (
            <div key={habit.id} className="habits-archive-row">
              <span className="habits-archive-row-name">{habit.name}</span>
              <div className="habits-archive-row-actions">
                <button className="habits-archive-btn" onClick={() => onUnarchive(habit.id)} title="Restore habit">
                  Restore
                </button>
                <button
                  className="habits-archive-btn habits-archive-btn--delete"
                  onClick={() => onDelete(habit.id)}
                  aria-label="Delete habit"
                  title="Delete permanently"
                >
                  <Cross2Icon />
                </button>
              </div>
            </div>
          ))}
        </div>
      </Collapsible>
    </div>
  )
}

const METRIC_OPTIONS = [
  { value: '', label: 'No metric' },
  { value: 'steps', label: '👟 Steps' },
  { value: 'fat_ratio', label: '⚖️ Body Fat %' },
  { value: 'weight', label: '🏋️ Weight (kg)' },
]

const METRIC_GOAL_PLACEHOLDER = { steps: '10000', fat_ratio: '20.0', weight: '75.0' }

function metricBadgeText(metric, goal) {
  if (metric === 'steps') return goal != null ? `${Math.round(goal).toLocaleString()} steps` : 'steps'
  if (metric === 'fat_ratio') return `body fat${goal != null ? ' ≤ ' + goal.toFixed(1) + '%' : ''}`
  if (metric === 'weight') return `weight${goal != null ? ' ≤ ' + goal.toFixed(1) + ' kg' : ''}`
  return metric
}

export default function HabitsPage({ habits, archivedHabits = [], allTags, selectedTagId = null, onToggle, onAdd, onUpdate, onDelete, onArchive, onUnarchive }) {
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const [editMetric, setEditMetric] = useState('')
  const [editGoal, setEditGoal] = useState('')
  const [poppingId, setPoppingId] = useState(null)
  const editInputRef = useRef(null)
  const popTimer = useRef(null)

  useEffect(() => {
    if (editingId !== null) editInputRef.current?.focus()
  }, [editingId])

  const startEdit = (habit) => {
    setEditingId(habit.id)
    setEditName(habit.name)
    setEditMetric(habit.withings_metric || '')
    setEditGoal(habit.withings_goal != null ? String(habit.withings_goal) : '')
  }

  const confirmEdit = async () => {
    const name = editName.trim()
    if (name) {
      await onUpdate(editingId, {
        name,
        withings_metric: editMetric || null,
        withings_goal: editMetric && editGoal !== '' ? parseFloat(editGoal) : null,
      })
    }
    setEditingId(null)
  }

  const visibleHabits = selectedTagId === null
    ? habits
    : habits.filter((h) => h.tags.some((t) => t.id === selectedTagId))

  const done = visibleHabits.filter((h) => h.completed_today).length

  return (
    <div className="habits-page">
      <div className="habits-page-header">
        <div className="habits-page-title-row">
          <h2 className="habits-page-title">Habits</h2>
          {visibleHabits.length > 0 && (
            <span className="habits-page-progress">{done} / {visibleHabits.length} today</span>
          )}
        </div>
      </div>

      {visibleHabits.length === 0 ? (
        <div className="habits-empty">
          {selectedTagId !== null
            ? 'No habits with this tag.'
            : 'No habits yet. Add your first one to start tracking daily streaks.'}
        </div>
      ) : (
        <div className="habits-list">
          {visibleHabits.map((habit) => (
            <div key={habit.id} className={`habit-card${habit.completed_today ? ' habit-card--done' : ''}`}>
              <button
                type="button"
                className={`habit-card-check${poppingId === habit.id ? ' habit-card-check--pop' : ''}`}
                onClick={habit.withings_metric ? undefined : () => {
                  setPoppingId(habit.id)
                  clearTimeout(popTimer.current)
                  popTimer.current = setTimeout(() => setPoppingId(null), 350)
                  onToggle(habit)
                }}
                disabled={!!habit.withings_metric}
                title={habit.withings_metric ? 'Auto-checked by health sync' : undefined}
                aria-label={habit.completed_today ? 'Mark incomplete' : 'Mark complete'}
              >
                {habit.completed_today ? <CheckIcon width={13} height={13} /> : null}
              </button>

              <div className="habit-card-body">
                {editingId === habit.id ? (
                  <div className="habit-card-edit-form">
                    <input
                      ref={editInputRef}
                      className="habit-card-edit-input"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Escape') setEditingId(null) }}
                      placeholder="Habit name"
                    />
                    <div className="habit-card-edit-metric-row">
                      <select
                        className="habit-card-edit-select"
                        value={editMetric}
                        onChange={(e) => { setEditMetric(e.target.value); setEditGoal('') }}
                      >
                        {METRIC_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                      {editMetric && (
                        <input
                          type="number"
                          className="habit-card-edit-goal"
                          value={editGoal}
                          onChange={(e) => setEditGoal(e.target.value)}
                          placeholder={METRIC_GOAL_PLACEHOLDER[editMetric] || 'Goal'}
                          min="0"
                          step={editMetric === 'steps' ? '500' : '0.1'}
                        />
                      )}
                    </div>
                    <div className="habit-card-edit-actions">
                      <button className="habit-card-edit-save" onClick={confirmEdit}>Save</button>
                      <button className="habit-card-edit-cancel" onClick={() => setEditingId(null)}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <span className="habit-card-name">{habit.name}</span>
                )}

                {editingId !== habit.id && habit.tags.length > 0 && (
                  <div className="habit-card-tags">
                    {habit.tags.map((tag) => (
                      <span
                        key={tag.id}
                        className="habit-card-tag"
                        style={{ background: `${tag.color}22`, color: tag.color, borderColor: `${tag.color}44` }}
                      >
                        {tag.name}
                      </span>
                    ))}
                  </div>
                )}

                {editingId !== habit.id && habit.withings_metric && (
                  <div className="habit-card-withings">
                    <span className="habit-card-withings-badge">
                      {METRIC_OPTIONS.find(o => o.value === habit.withings_metric)?.label.split(' ')[0] || '📊'}
                      {' '}
                      {metricBadgeText(habit.withings_metric, habit.withings_goal)}
                    </span>
                    <span className="habit-card-withings-auto">auto</span>
                  </div>
                )}

                {habit.recent_completions?.length > 0 && (
                  <div className="habit-card-history">
                    <div className="habit-dots">
                      {habit.recent_completions.map((done, i) => (
                        <span
                          key={i}
                          className={`habit-dot${done ? ' habit-dot--done' : ''}${i === 6 ? ' habit-dot--today' : ''}`}
                        />
                      ))}
                    </div>
                    {habit.streak > 0 && (
                      <span className="habit-card-streak">🔥 {habit.streak}</span>
                    )}
                  </div>
                )}
              </div>

              <div className="habit-card-actions">
                <button
                  type="button"
                  className="habit-card-btn"
                  onClick={() => startEdit(habit)}
                  aria-label="Edit habit"
                  title="Edit name"
                >
                  <Pencil1Icon width={13} height={13} />
                </button>
                <button
                  type="button"
                  className="habit-card-btn habit-card-btn--archive"
                  onClick={() => onArchive(habit.id)}
                  aria-label="Archive habit"
                  title="Archive habit"
                >
                  <svg width="13" height="13" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="1" y="1" width="13" height="3.5" rx="0.5" stroke="currentColor" strokeWidth="1.2"/>
                    <path d="M1.5 4.5v8.5a.5.5 0 00.5.5h11a.5.5 0 00.5-.5V4.5" stroke="currentColor" strokeWidth="1.2"/>
                    <path d="M5.5 8.5l2 2 2-2M7.5 10.5V7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <HabitsArchive
        habits={archivedHabits}
        onUnarchive={onUnarchive}
        onDelete={onDelete}
      />

    </div>
  )
}
