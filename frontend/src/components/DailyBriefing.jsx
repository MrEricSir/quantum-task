import { useState, useEffect, useRef } from 'react'
import { UpdateIcon, ExclamationTriangleIcon } from '@radix-ui/react-icons'
import './DailyBriefing.css'

export default function DailyBriefing({ todos, calendarEvents, habits = [], tagId = null, ready = true, onWeather, todayOnly = false }) {
  const [sections, setSections] = useState({ today: '', week: '' })
  const [status, setStatus] = useState('idle') // idle | loading | done | error
  const [error, setError] = useState('')
  const abortRef = useRef(null)
  const todosRef = useRef(todos)
  const calEventsRef = useRef(calendarEvents)
  const habitsRef = useRef(habits)
  todosRef.current = todos
  calEventsRef.current = calendarEvents
  habitsRef.current = habits

  const getLocation = () =>
    new Promise((resolve) => {
      if (!navigator.geolocation) return resolve(null)
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
        () => resolve(null),
        { timeout: 5000 },
      )
    })

  const generate = async (force = false) => {
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setError('')
    setStatus('loading')

    const location = await getLocation()

    try {
      const response = await fetch('/api/briefing/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          todos: todosRef.current,
          calendar_events: calEventsRef.current,
          habits: habitsRef.current.map(({ name, completed_today }) => ({ name, completed_today })),
          force,
          today_only: todayOnly,
          ...(location ?? {}),
        }),
        signal: controller.signal,
      })
      if (!response.ok) throw new Error('Server error')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      const accumulated = { today: '', week: '' }

      outer: while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6).trim()
          if (data === '[DONE]') break outer
          try {
            const parsed = JSON.parse(data)
            if (parsed.error) {
              setError(parsed.error || 'Could not generate briefing.')
              setStatus('error')
              return
            }
            if (parsed.type === 'weather') { onWeather?.(parsed); continue }
            if (parsed.section && parsed.text) {
              accumulated[parsed.section] = (accumulated[parsed.section] || '') + parsed.text
            }
          } catch {
            // malformed chunk, skip
          }
        }
      }

      setSections(accumulated)
      setStatus('done')
    } catch (err) {
      if (err.name !== 'AbortError') {
        setError('Could not generate briefing.')
        setStatus('error')
      }
    }
  }

  useEffect(() => {
    if (!ready) return
    generate()
    return () => abortRef.current?.abort()
  }, [tagId, ready]) // eslint-disable-line react-hooks/exhaustive-deps

  const hasContent = sections.today || sections.week

  // Strip markdown bullet markers that the model sometimes emits despite instructions
  function cleanBriefingText(text) {
    if (!text) return text
    // Handle "Intro text: * item * item" inline pattern
    let s = text.replace(/^[^*\n\-•]+[:\-]\s*(?=[*\-•])/, '')
    // Normalize newline+bullet and inline " * " separators into newlines
    s = s.replace(/\n\s*[*\-•]\s*/g, '\n').replace(/\s{2,}\*\s+/g, '\n')
    // Strip any remaining leading bullet on first line
    s = s.replace(/^[*\-•]\s+/, '')
    return s.trim()
  }

  if (status === 'idle') return null

  if (status === 'error') return (
    <div className="briefing briefing--error">
      <span className="briefing-icon"><ExclamationTriangleIcon /></span>
      <span className="briefing-error-text">{error}</span>
      <button className="briefing-refresh" onClick={generate}>Retry</button>
    </div>
  )

  if (status === 'loading' && !hasContent) return (
    <div className="briefing">
      <div className="briefing-sections">
        <span className="briefing-spinner" />
      </div>
    </div>
  )

  return (
    <div className="briefing">
      <div className="briefing-sections">
        {sections.today && (
          <div className="briefing-row">
            <span className="briefing-label">Today</span>
            <span className="briefing-text" style={{ whiteSpace: 'pre-line' }}>{cleanBriefingText(sections.today)}</span>
          </div>
        )}
        {sections.week && !todayOnly && (
          <div className="briefing-row">
            <span className="briefing-label">This week</span>
            <span className="briefing-text" style={{ whiteSpace: 'pre-line' }}>{cleanBriefingText(sections.week)}</span>
          </div>
        )}
      </div>
      {(status === 'done' || (status === 'loading' && hasContent)) && (
        <button
          className="briefing-refresh"
          onClick={() => generate(true)}
          disabled={status === 'loading'}
          title="Regenerate"
        >
          {status === 'loading' ? <span className="briefing-spin" /> : <UpdateIcon />}
        </button>
      )}
    </div>
  )
}
