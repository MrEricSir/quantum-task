import { useState, useEffect, useCallback } from 'react'
import { fetchInsights, updateCard } from '../../api'
import './InsightsPanel.css'

const SNOOZE_OPTIONS = [
  { label: '3 days', days: 3 },
  { label: '1 week', days: 7 },
  { label: '2 weeks', days: 14 },
]

const MOVE_OPTIONS = [
  { label: 'This Week', section: 'week' },
  { label: 'This Month', section: 'month' },
  { label: 'Stash', section: 'later' },
]

function addDays(days) {
  const d = new Date()
  d.setDate(d.getDate() + days)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function insightKey(ins) {
  if (ins.type === 'stuck_task') return `task-${ins.card.id}`
  if (ins.type === 'habit_trend') return `habit-${ins.habit_id}`
  return `health-${ins.metric}-${ins.type}` // health_trend | health_no_data
}

function StuckTaskInsight({ insight, onDismiss, onArchive }) {
  const { card, text, days_stuck } = insight
  const [mode, setMode] = useState(null) // null | 'reschedule' | 'snooze'
  const [reason, setReason] = useState('')
  const [date, setDate] = useState('')
  const [busy, setBusy] = useState(false)
  const key = insightKey(insight)

  async function handleMove(section) {
    setBusy(true)
    try {
      await updateCard(card.id, { section })
      onDismiss(key)
    } finally {
      setBusy(false)
    }
  }

  async function handleSetDate() {
    if (!date) return
    setBusy(true)
    try {
      await updateCard(card.id, { scheduled_at: date + 'T00:00:00', section: 'week' })
      onDismiss(key)
    } finally {
      setBusy(false)
    }
  }

  async function handleSnooze(days) {
    setBusy(true)
    try {
      await updateCard(card.id, {
        snoozed_until: addDays(days),
        waiting_reason: reason.trim() || null,
      })
      onDismiss(key)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="insight-card insight-card--stuck">
      <div className="insight-header">
        <span className="insight-icon">&#9201;</span>
        <div className="insight-body">
          <p className="insight-text">{text}</p>
          <p className="insight-meta">{card.title} &middot; {days_stuck}d in Today</p>
        </div>
      </div>

      {mode === 'reschedule' && (
        <div className="insight-reschedule-form">
          <div className="insight-section-btns">
            {MOVE_OPTIONS.map(opt => (
              <button
                key={opt.section}
                className="insight-btn"
                onClick={() => handleMove(opt.section)}
                disabled={busy}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <div className="insight-date-row">
            <input
              type="date"
              className="insight-date-input"
              value={date}
              min={addDays(1)}
              onChange={e => setDate(e.target.value)}
            />
            {date && (
              <button className="insight-btn" onClick={handleSetDate} disabled={busy}>
                Set date
              </button>
            )}
          </div>
          <button className="insight-btn insight-btn--cancel" onClick={() => setMode(null)}>
            Cancel
          </button>
        </div>
      )}

      {mode === 'snooze' && (
        <div className="insight-snooze-form">
          <input
            className="insight-reason-input"
            placeholder="Waiting on… (optional)"
            value={reason}
            onChange={e => setReason(e.target.value)}
            autoFocus
          />
          <div className="insight-snooze-options">
            {SNOOZE_OPTIONS.map(opt => (
              <button
                key={opt.days}
                className="insight-btn insight-btn--snooze"
                onClick={() => handleSnooze(opt.days)}
                disabled={busy}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <button className="insight-btn insight-btn--cancel" onClick={() => setMode(null)}>
            Cancel
          </button>
        </div>
      )}

      {!mode && (
        <div className="insight-actions">
          <button className="insight-btn" onClick={() => setMode('reschedule')} disabled={busy}>
            Reschedule
          </button>
          <button className="insight-btn" onClick={() => setMode('snooze')} disabled={busy}>
            Snooze
          </button>
          <button
            className="insight-btn insight-btn--danger"
            onClick={() => onArchive(insight)}
            disabled={busy}
          >
            Archive
          </button>
        </div>
      )}
    </div>
  )
}

function HabitInsight({ insight, onDismiss }) {
  const { habit_name, text, completions_last_7 } = insight
  return (
    <div className="insight-card insight-card--habit">
      <div className="insight-header">
        <span className="insight-icon">&#128200;</span>
        <div className="insight-body">
          <p className="insight-text">{text}</p>
          <p className="insight-meta">{habit_name} &middot; {completions_last_7}/7 days</p>
        </div>
        <button
          className="insight-dismiss"
          onClick={() => onDismiss(insightKey(insight))}
          title="Dismiss"
        >
          &#10005;
        </button>
      </div>
    </div>
  )
}

function HealthInsight({ insight, onDismiss }) {
  const { text, type } = insight
  const icon = type === 'health_no_data' ? '\u{1F4CF}' : '\u{1F4C8}'
  return (
    <div className="insight-card insight-card--health">
      <div className="insight-header">
        <span className="insight-icon">{icon}</span>
        <div className="insight-body">
          <p className="insight-text">{text}</p>
        </div>
        <button
          className="insight-dismiss"
          onClick={() => onDismiss(insightKey(insight))}
          title="Dismiss"
        >
          &#10005;
        </button>
      </div>
    </div>
  )
}

export default function InsightsPanel({ refreshKey, onArchive }) {
  const [insights, setInsights] = useState([])
  const [loading, setLoading] = useState(true)
  const [dismissed, setDismissed] = useState(new Set())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchInsights()
      setInsights(data)
      setDismissed(new Set())
    } catch {
      // silently fail — insights are non-critical
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load, refreshKey])

  const handleDismiss = useCallback((key) => {
    setDismissed(prev => new Set([...prev, key]))
  }, [])

  const handleArchive = useCallback(async (insight) => {
    setDismissed(prev => new Set([...prev, insightKey(insight)]))
    await onArchive(insight.card)
  }, [onArchive])

  const visible = insights.filter(ins => !dismissed.has(insightKey(ins)))

  if (loading || visible.length === 0) return null

  return (
    <section className="insights-panel">
      <h3 className="insights-title">Needs attention</h3>
      <div className="insights-list">
        {visible.map((ins) => {
          const key = insightKey(ins)
          if (ins.type === 'stuck_task') return (
            <StuckTaskInsight key={key} insight={ins} onDismiss={handleDismiss} onArchive={handleArchive} />
          )
          if (ins.type === 'habit_trend') return (
            <HabitInsight key={key} insight={ins} onDismiss={handleDismiss} />
          )
          return (
            <HealthInsight key={key} insight={ins} onDismiss={handleDismiss} />
          )
        })}
      </div>
    </section>
  )
}
