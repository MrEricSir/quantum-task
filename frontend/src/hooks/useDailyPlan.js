import { useState, useEffect, useCallback } from 'react'

function todayDateKey() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `daily-plan-${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const planHabit = h => !h.withings_metric && !h.is_experiment

function computeFingerprint(todos, events, habits) {
  return [
    todos.map(t => t.id).sort().join(','),
    events.map(e => e.id).sort().join(','),
    habits.filter(planHabit).map(h => h.id).sort().join(','),
  ].join('|')
}

function localDateHeader() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function toHHMM(isoString) {
  const d = new Date(isoString)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// Build a best-effort plan immediately from local data — shown while the LLM runs.
// Calendar events and timed tasks are anchored; everything else goes to overflow.
function computePreflightBlocks(todos, events, habits) {
  const anchored = []

  for (const e of events) {
    if (e.all_day || !e.start) continue
    const duration = e.end
      ? Math.round((new Date(e.end) - new Date(e.start)) / 60000)
      : null
    anchored.push({ time: toHHMM(e.start), title: e.title, type: 'event', duration, fixed: true })
  }

  for (const t of todos) {
    if (t.completed || !t.scheduled_at || t.section !== 'today') continue
    anchored.push({ time: toHHMM(t.scheduled_at), title: t.title, type: 'task', fixed: true })
  }

  anchored.sort((a, b) => (a.time > b.time ? 1 : -1))

  const overflow = []
  for (const t of todos) {
    if (t.completed || t.scheduled_at || t.section !== 'today') continue
    overflow.push({ time: null, title: t.title, type: 'task' })
  }
  for (const h of habits) {
    if (h.completed_today || !planHabit(h)) continue
    overflow.push({ time: null, title: h.name, type: 'habit' })
  }

  return [...anchored, ...overflow]
}

export function useDailyPlan(todos, events, habits) {
  const [status, setStatus] = useState('idle')   // idle | loading | done | error
  const [blocks, setBlocks] = useState([])
  const [planFingerprint, setPlanFingerprint] = useState(null)
  const [error, setError] = useState('')

  // Restore today's plan from localStorage on mount
  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(todayDateKey()) || 'null')
      if (stored?.blocks) {
        setBlocks(stored.blocks)
        setPlanFingerprint(stored.fingerprint ?? null)
        setStatus('done')
      }
    } catch {}
  }, [])

  // Reset to idle when the day rolls over (localStorage key changes at midnight)
  useEffect(() => {
    if (status === 'idle') return
    const id = setInterval(() => {
      if (!localStorage.getItem(todayDateKey())) {
        setStatus('idle')
        setBlocks([])
        setPlanFingerprint(null)
        setError('')
      }
    }, 60_000)
    return () => clearInterval(id)
  }, [status])

  // Remove completed/experiment/withings habits from blocks whenever habits change
  useEffect(() => {
    if (status !== 'done' && status !== 'loading') return
    const excludedNames = new Set(
      habits.filter(h => h.completed_today || !planHabit(h)).map(h => h.name)
    )
    if (excludedNames.size === 0) return
    setBlocks(prev => {
      const next = prev.filter(b => b.type !== 'habit' || !excludedNames.has(b.title))
      if (next.length === prev.length) return prev
      try {
        const stored = JSON.parse(localStorage.getItem(todayDateKey()) || 'null')
        if (stored) localStorage.setItem(todayDateKey(), JSON.stringify({ ...stored, blocks: next }))
      } catch {}
      return next
    })
  }, [habits, status])

  const currentFingerprint = computeFingerprint(todos, events, habits)
  const isStale = status === 'done' && planFingerprint !== null && currentFingerprint !== planFingerprint

  const generate = useCallback(async () => {
    setBlocks(computePreflightBlocks(todos, events, habits))
    setStatus('loading')
    setError('')
    try {
      const res = await fetch('/api/daily-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Local-Date': localDateHeader() },
        body: JSON.stringify({
          todos,
          calendar_events: events,
          habits: habits.filter(planHabit).map(({ name, completed_today }) => ({ name, completed_today })),
          utc_offset_minutes: new Date().getTimezoneOffset(),
        }),
      })
      if (!res.ok) throw new Error('Server error')
      const data = await res.json()
      const newBlocks = data.blocks || []
      const fp = computeFingerprint(todos, events, habits)
      setBlocks(newBlocks)
      setPlanFingerprint(fp)
      setStatus('done')
      try {
        localStorage.setItem(todayDateKey(), JSON.stringify({ blocks: newBlocks, fingerprint: fp }))
      } catch {}
    } catch {
      setError('Could not generate plan.')
      setStatus('error')
    }
  }, [todos, events, habits])

  const dismiss = useCallback(() => {
    setStatus('idle')
    setBlocks([])
    setPlanFingerprint(null)
    try { localStorage.removeItem(todayDateKey()) } catch {}
  }, [])

  return { status, blocks, isStale, error, generate, dismiss }
}
