import { useState, useEffect, useCallback } from 'react'
import { fetchInsights, updateCard } from '../../api'
import './InsightsPanel.css'

const SNOOZE_OPTIONS = [
  { label: '3 days', days: 3 },
  { label: '1 week', days: 7 },
  { label: '2 weeks', days: 14 },
]

function addDays(days) {
  const d = new Date()
  d.setDate(d.getDate() + days)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function StuckTaskInsight({ insight, onDismiss, onEdit, onArchive }) {
  const { card, text, days_stuck } = insight
  const [snoozing, setSnoozing] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSnooze(days) {
    setBusy(true)
    try {
      await updateCard(card.id, {
        snoozed_until: addDays(days),
        waiting_reason: reason.trim() || null,
      })
      onDismiss(card.id)
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

      {snoozing ? (
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
          <button className="insight-btn insight-btn--cancel" onClick={() => setSnoozing(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="insight-actions">
          <button className="insight-btn" onClick={() => onEdit(card)} disabled={busy}>
            Reschedule
          </button>
          <button className="insight-btn" onClick={() => setSnoozing(true)} disabled={busy}>
            Snooze
          </button>
          <button
            className="insight-btn insight-btn--danger"
            onClick={() => onArchive(card)}
            disabled={busy}
          >
            Archive
          </button>
        </div>
      )}
    </div>
  )
}

function HabitInsight({ insight }) {
  const { habit_name, text, completions_last_7 } = insight
  return (
    <div className="insight-card insight-card--habit">
      <div className="insight-header">
        <span className="insight-icon">&#128200;</span>
        <div className="insight-body">
          <p className="insight-text">{text}</p>
          <p className="insight-meta">{habit_name} &middot; {completions_last_7}/7 days</p>
        </div>
      </div>
    </div>
  )
}

export default function InsightsPanel({ refreshKey, onEdit, onArchive }) {
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

  const handleDismiss = useCallback((cardId) => {
    setDismissed(prev => new Set([...prev, cardId]))
  }, [])

  const handleArchive = useCallback(async (card) => {
    setDismissed(prev => new Set([...prev, card.id]))
    await onArchive(card)
  }, [onArchive])

  const visible = insights.filter(ins =>
    ins.type !== 'stuck_task' || !dismissed.has(ins.card?.id)
  )

  if (loading || visible.length === 0) return null

  return (
    <section className="insights-panel">
      <h3 className="insights-title">Needs attention</h3>
      <div className="insights-list">
        {visible.map((ins, i) =>
          ins.type === 'stuck_task' ? (
            <StuckTaskInsight
              key={ins.card.id}
              insight={ins}
              onDismiss={handleDismiss}
              onEdit={onEdit}
              onArchive={handleArchive}
            />
          ) : (
            <HabitInsight key={`habit-${ins.habit_id}-${i}`} insight={ins} />
          )
        )}
      </div>
    </section>
  )
}
