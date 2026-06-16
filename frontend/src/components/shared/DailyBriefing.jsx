import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { UpdateIcon, ExclamationTriangleIcon, SpeakerLoudIcon, StopIcon } from '@radix-ui/react-icons'
import './DailyBriefing.css'

export default function DailyBriefing({ todos, calendarEvents, habits = [], tagId = null, ready = true, onWeather, todayOnly = false, invalidationKey = 0 }) {
  const [sections, setSections] = useState({ today: '', week: '' })
  const [status, setStatus] = useState('idle') // idle | loading | done | error
  const [error, setError] = useState('')
  const [speaking, setSpeaking] = useState(false)
  const abortRef = useRef(null)
  const todosRef = useRef(todos)
  const calEventsRef = useRef(calendarEvents)
  const habitsRef = useRef(habits)
  todosRef.current = todos
  calEventsRef.current = calendarEvents
  habitsRef.current = habits
  const mountedRef = useRef(false)
  const debounceRef = useRef(null)
  const generateRef = useRef(null)
  const containerRef = useRef(null)
  const prevHeightRef = useRef(0)

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
      const d = new Date()
      const pad = n => String(n).padStart(2, '0')
      const localDate = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
      const response = await fetch('/api/briefing/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Local-Date': localDate },
        body: JSON.stringify({
          todos: todosRef.current,
          calendar_events: calEventsRef.current,
          habits: habitsRef.current.map(({ name, completed_today }) => ({ name, completed_today })),
          force,
          today_only: todayOnly,
          utc_offset_minutes: new Date().getTimezoneOffset(),
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

  generateRef.current = generate

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const newHeight = el.offsetHeight
    if (prevHeightRef.current > 0 && prevHeightRef.current !== newHeight) {
      el.animate(
        [{ height: `${prevHeightRef.current}px` }, { height: `${newHeight}px` }],
        { duration: 250, easing: 'ease-out', fill: 'none' },
      )
    }
    prevHeightRef.current = newHeight
  }, [sections, status])

  useEffect(() => {
    if (!ready) return
    generate()
    return () => {
      abortRef.current?.abort()
      window.speechSynthesis.cancel()
    }
  }, [tagId, ready]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-refresh with debounce when upstream data changes (new tasks, calendar
  // events, habit toggles). Skip the initial mount — generate() handles that.
  useEffect(() => {
    if (!mountedRef.current) { mountedRef.current = true; return }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      generateRef.current(true)
      debounceRef.current = null
    }, 10_000)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [invalidationKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const hasContent = sections.today || sections.week

  // Collapse newlines and strip any bullet markers the model emits despite instructions
  function cleanBriefingText(text) {
    if (!text) return text
    let s = text
    s = s.replace(/\n+/g, ' ')
    s = s.replace(/\s*[*\-•]\s+/g, ' ')
    s = s.replace(/  +/g, ' ')
    return s.trim()
  }

  const handleSpeak = () => {
    if (speaking) {
      window.speechSynthesis.cancel()
      setSpeaking(false)
      return
    }
    const text = [sections.today, sections.week]
      .map(cleanBriefingText)
      .filter(Boolean)
      .join(' ')
    if (!text) return
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.onend = () => setSpeaking(false)
    utterance.onerror = () => setSpeaking(false)
    setSpeaking(true)
    window.speechSynthesis.speak(utterance)
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
    <div className="briefing" ref={containerRef}>
      <div className="briefing-sections">
        <span className="briefing-spinner" />
      </div>
    </div>
  )

  return (
    <div className="briefing" ref={containerRef}>
      <div className="briefing-sections">
        {sections.today && (
          <div className="briefing-row">
            <span className="briefing-label">Today</span>
            <span className="briefing-text">{cleanBriefingText(sections.today)}</span>
          </div>
        )}
        {sections.week && !todayOnly && (
          <div className="briefing-row">
            <span className="briefing-label">This week</span>
            <span className="briefing-text">{cleanBriefingText(sections.week)}</span>
          </div>
        )}
      </div>
      {status === 'done' && (
        <button
          className={`briefing-listen${speaking ? ' briefing-listen--active' : ''}`}
          onClick={handleSpeak}
          title={speaking ? 'Stop' : 'Listen'}
        >
          {speaking ? <StopIcon /> : <SpeakerLoudIcon />}
        </button>
      )}
      {(status === 'done' || (status === 'loading' && hasContent)) && (
        <button
          className="briefing-refresh"
          onClick={() => { window.speechSynthesis.cancel(); setSpeaking(false); generate(true) }}
          disabled={status === 'loading'}
          title="Regenerate"
        >
          {status === 'loading' ? <span className="briefing-spin" /> : <UpdateIcon />}
        </button>
      )}
    </div>
  )
}
