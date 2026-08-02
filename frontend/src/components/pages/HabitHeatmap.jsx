import { useState, useEffect } from 'react'
import { fetchHabitStreakDays, localDateOf } from '../../api'

const HEATMAP_DAYS = 84 // ~12 weeks

export default function HabitHeatmap({ habitId }) {
  const [completedDates, setCompletedDates] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    const to = new Date()
    const from = new Date()
    from.setDate(from.getDate() - (HEATMAP_DAYS - 1))

    fetchHabitStreakDays(habitId, localDateOf(from), localDateOf(to))
      .then((rows) => {
        if (cancelled) return
        setCompletedDates(new Set(rows.map((r) => r.date)))
      })
      .catch(() => { if (!cancelled) setError('Failed to load history') })

    return () => { cancelled = true }
  }, [habitId])

  if (error) return <div className="habit-heatmap-error">{error}</div>
  if (!completedDates) return <div className="habit-heatmap-loading">Loading history…</div>

  const days = []
  const cursor = new Date()
  cursor.setDate(cursor.getDate() - (HEATMAP_DAYS - 1))
  for (let i = 0; i < HEATMAP_DAYS; i++) {
    const dateStr = localDateOf(cursor)
    days.push({ date: dateStr, done: completedDates.has(dateStr) })
    cursor.setDate(cursor.getDate() + 1)
  }

  return (
    <div className="habit-heatmap" role="img" aria-label={`Completion history, last ${HEATMAP_DAYS} days`}>
      {days.map((d) => (
        <span
          key={d.date}
          className={`habit-heatmap-cell${d.done ? ' habit-heatmap-cell--done' : ''}`}
          title={d.date}
        />
      ))}
    </div>
  )
}
