import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react'
import { UpdateIcon, ExclamationTriangleIcon, SpeakerLoudIcon, StopIcon, ChevronRightIcon } from '@radix-ui/react-icons'
import { localDate } from '../../api'
import './DailyBriefing.css'

export default function DailyBriefing({ tagId = null, ready = true, onWeather, todayOnly = false, invalidationKey = 0, collapsedByDefault = false }) {
  const [sections, setSections] = useState({ today: '', week: '' })
  const [expanded, setExpanded] = useState(!collapsedByDefault)
  // Initialize straight into 'loading' when already ready and expanded, so the
  // very first render already reserves space instead of appearing a frame later.
  const [status, setStatus] = useState(ready && expanded ? 'loading' : 'idle') // idle | loading | done | error
  const [showSpinner, setShowSpinner] = useState(false)
  const [error, setError] = useState('')
  const [speaking, setSpeaking] = useState(false)
  const abortRef = useRef(null)
  const mountedRef = useRef(false)
  const debounceRef = useRef(null)
  const generateRef = useRef(null)
  const containerRef = useRef(null)
  const prevHeightRef = useRef(0)

  const getLocation = useCallback(() => {
    const cachedLocation = () => {
      try {
        const cached = localStorage.getItem('briefing-last-location')
        return cached ? JSON.parse(cached) : null
      } catch {
        return null
      }
    }
    if (!navigator.geolocation) return Promise.resolve(cachedLocation())
    // Wait for a live reading first -- resolving from the cache immediately would
    // keep showing the pre-trip city's weather for every briefing fetched right after
    // landing somewhere new, until some later unrelated call happened to refresh the
    // cache. Only fall back to the cache if geolocation is denied or times out.
    return new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const loc = { lat: pos.coords.latitude, lon: pos.coords.longitude }
          try { localStorage.setItem('briefing-last-location', JSON.stringify(loc)) } catch {}
          resolve(loc)
        },
        () => resolve(cachedLocation()),
        { timeout: 10000 },
      )
    })
  }, [])

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
        headers: { 'Content-Type': 'application/json', 'X-Local-Date': localDate(), 'X-UTC-Offset': String(new Date().getTimezoneOffset()) },
        body: JSON.stringify({
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

  generateRef.current = generate

  // Only show the spinner after 400 ms — cached responses arrive before that,
  // so they appear directly without any spinner flash.
  useEffect(() => {
    if (status !== 'loading') { setShowSpinner(false); return }
    const t = setTimeout(() => setShowSpinner(true), 400)
    return () => clearTimeout(t)
  }, [status])

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
    if (!ready || !expanded) return
    generate()
    return () => {
      abortRef.current?.abort()
      window.speechSynthesis.cancel()
    }
  }, [tagId, ready, expanded]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-refresh with debounce when upstream data changes (new tasks, calendar
  // events, habit toggles). Skip the initial mount — generate() handles that.
  // Skip entirely while collapsed -- expanding is what triggers the first
  // generate() (via the effect above), not a background refresh nobody asked for.
  useEffect(() => {
    if (!mountedRef.current) { mountedRef.current = true; return }
    if (!expanded) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      generateRef.current(false)
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

  if (status === 'idle') {
    if (!expanded && ready) return (
      <button type="button" className="briefing briefing--collapsed" onClick={() => setExpanded(true)}>
        <span className="briefing-collapsed-text">Daily briefing</span>
        <span className="briefing-icon"><ChevronRightIcon /></span>
      </button>
    )
    return null
  }

  if (status === 'error') return (
    <div className="briefing briefing--error">
      <span className="briefing-icon"><ExclamationTriangleIcon /></span>
      <span className="briefing-error-text">{error}</span>
      <button className="briefing-refresh" onClick={generate}>Retry</button>
    </div>
  )

  return (
    <div className="briefing" ref={containerRef}>
      <div className="briefing-sections">
        {hasContent ? (
          <>
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
          </>
        ) : (
          // Always reserve the spinner's space during load; only its
          // visibility is delayed, so the card never jumps in height.
          <span className="briefing-spinner" style={{ visibility: showSpinner ? 'visible' : 'hidden' }} />
        )}
      </div>
      <div className="briefing-actions">
        <button
          className={`briefing-listen${speaking ? ' briefing-listen--active' : ''}`}
          style={{ visibility: status === 'done' ? 'visible' : 'hidden' }}
          onClick={handleSpeak}
          title={speaking ? 'Stop' : 'Listen'}
          tabIndex={status === 'done' ? 0 : -1}
        >
          {speaking ? <StopIcon /> : <SpeakerLoudIcon />}
        </button>
        <button
          className="briefing-refresh"
          style={{ visibility: hasContent ? 'visible' : 'hidden' }}
          onClick={() => { window.speechSynthesis.cancel(); setSpeaking(false); generate(true) }}
          disabled={status === 'loading'}
          title="Regenerate"
          tabIndex={hasContent ? 0 : -1}
        >
          {status === 'loading' ? <span className="briefing-spin" /> : <UpdateIcon />}
        </button>
      </div>
    </div>
  )
}
