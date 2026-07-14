import { useState, useEffect, useCallback, useMemo } from 'react'
import { fetchInsights, updateCard } from '../../api'
import './InsightsPanel.css'

// ── Habit snooze via localStorage ─────────────────────────────────────────────
const SNOOZE_KEY = 'insights_snooze'

function getSnoozed() {
  try { return JSON.parse(localStorage.getItem(SNOOZE_KEY) || '{}') } catch { return {} }
}

function snoozeHabitInsight(key, days) {
  const map = getSnoozed()
  const d = new Date()
  d.setDate(d.getDate() + days)
  map[key] = d.toISOString().slice(0, 10)
  localStorage.setItem(SNOOZE_KEY, JSON.stringify(map))
}

function isHabitSnoozed(key) {
  const exp = getSnoozed()[key]
  if (!exp) return false
  return exp >= new Date().toISOString().slice(0, 10)
}

const SNOOZE_OPTIONS = [
  { label: '3 days', days: 3 },
  { label: '1 week', days: 7 },
  { label: '2 weeks', days: 14 },
]

const MOVE_OPTIONS = [
  { label: 'This Week', section: 'week' },
  { label: 'This Month', section: 'month' },
  { label: 'Later', section: 'later' },
]

function addDays(days) {
  const d = new Date()
  d.setDate(d.getDate() + days)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function insightKey(ins) {
  if (ins.type === 'stuck_task')        return `task-${ins.card.id}`
  if (ins.type === 'habit_trend')       return `habit-${ins.habit_id}`
  if (ins.type === 'completion_pattern') return `pattern-${ins.peak_window}`
  return `health-${ins.metric}-${ins.type}` // health_trend | health_no_data | health_bp
}

function StuckTaskInsight({ insight, onDismiss, onArchive }) {
  const { card, text, days_stuck } = insight
  const [mode, setMode] = useState(null) // null | 'reschedule' | 'snooze'
  const [reason, setReason] = useState('')
  const [date, setDate] = useState(card.scheduled_at ? card.scheduled_at.slice(0, 10) : '')
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
            <button className="insight-btn" onClick={handleSetDate} disabled={busy || !date}>
              Set date
            </button>
            <button className="insight-btn insight-btn--cancel" onClick={() => setMode(null)}>
              Cancel
            </button>
          </div>
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

const HABIT_SNOOZE_OPTIONS = [
  { label: 'Tomorrow', days: 1 },
  { label: '3 days', days: 3 },
]

function HabitInsight({ insight, onDismiss }) {
  const { habit_name, text, completions_last_7 } = insight
  const key = insightKey(insight)
  const [showSnooze, setShowSnooze] = useState(false)

  const handleSnooze = (days) => {
    snoozeHabitInsight(key, days)
    onDismiss(key)
  }

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
          onClick={() => setShowSnooze((v) => !v)}
          title="Snooze options"
        >
          &#10005;
        </button>
      </div>
      {showSnooze && (
        <div className="insight-actions">
          {HABIT_SNOOZE_OPTIONS.map((opt) => (
            <button
              key={opt.days}
              className="insight-btn insight-btn--snooze"
              onClick={() => handleSnooze(opt.days)}
            >
              Snooze {opt.label}
            </button>
          ))}
          <button className="insight-btn" onClick={() => onDismiss(key)}>
            Dismiss
          </button>
          <button className="insight-btn insight-btn--cancel" onClick={() => setShowSnooze(false)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}

function CompletionPatternInsight({ insight, onDismiss }) {
  const { text, peak_window } = insight
  const key = insightKey(insight)
  return (
    <div className="insight-card insight-card--pattern">
      <div className="insight-header">
        <span className="insight-icon">&#9200;</span>
        <div className="insight-body">
          <p className="insight-text">{text}</p>
          <p className="insight-meta">Based on your recent completions</p>
        </div>
        <button
          className="insight-dismiss"
          onClick={() => onDismiss(key)}
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
      // Don't reset dismissed — keep session dismissals across refreshes
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

  const visible = useMemo(() =>
    insights.filter((ins) => {
      if (dismissed.has(insightKey(ins))) return false
      if (ins.type === 'habit_trend' && isHabitSnoozed(insightKey(ins))) return false
      return true
    }),
    [insights, dismissed]
  )

  if (loading || visible.length === 0) return null

  return (
    <section className="insights-panel">
      <h3 className="insights-title">Insights</h3>
      <div className="insights-list">
        {visible.map((ins) => {
          const key = insightKey(ins)
          if (ins.type === 'stuck_task') return (
            <StuckTaskInsight key={key} insight={ins} onDismiss={handleDismiss} onArchive={handleArchive} />
          )
          if (ins.type === 'habit_trend') return (
            <HabitInsight key={key} insight={ins} onDismiss={handleDismiss} />
          )
          if (ins.type === 'completion_pattern') return (
            <CompletionPatternInsight key={key} insight={ins} onDismiss={handleDismiss} />
          )
          return (
            <HealthInsight key={key} insight={ins} onDismiss={handleDismiss} />
          )
        })}
      </div>
    </section>
  )
}
