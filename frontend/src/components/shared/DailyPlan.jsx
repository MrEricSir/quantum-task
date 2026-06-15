import { useState } from 'react'
import { UpdateIcon } from '@radix-ui/react-icons'
import './DailyPlan.css'

const TYPE_COLOR = {
  event:  'var(--color-today)',
  task:   '#8b5cf6',
  habit:  '#10b981',
  break:  'rgba(255,255,255,0.18)',
}

function fmt12h(time24) {
  if (!time24) return null
  const [h, m] = time24.split(':').map(Number)
  const period = h >= 12 ? 'PM' : 'AM'
  const h12 = h % 12 || 12
  return m === 0 ? `${h12} ${period}` : `${h12}:${String(m).padStart(2, '0')} ${period}`
}

function fmtDuration(min) {
  if (!min) return ''
  if (min < 60) return `${min}m`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m === 0 ? `${h}h` : `${h}h ${m}m`
}

function localDateHeader() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export default function DailyPlan({ todos, calendarEvents, habits }) {
  const [status, setStatus]  = useState('idle')
  const [blocks, setBlocks]  = useState([])
  const [error,  setError]   = useState('')

  const generate = async () => {
    setStatus('loading')
    setError('')
    try {
      const res = await fetch('/api/daily-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Local-Date': localDateHeader() },
        body: JSON.stringify({
          todos,
          calendar_events: calendarEvents,
          habits: habits.map(({ name, completed_today }) => ({ name, completed_today })),
          utc_offset_minutes: new Date().getTimezoneOffset(),
        }),
      })
      if (!res.ok) throw new Error('Server error')
      const data = await res.json()
      setBlocks(data.blocks || [])
      setStatus('done')
    } catch {
      setError('Could not generate plan.')
      setStatus('error')
    }
  }

  if (status === 'idle') {
    return (
      <div className="daily-plan daily-plan--idle">
        <button className="daily-plan-trigger" onClick={generate}>
          ✦ Plan my day
        </button>
      </div>
    )
  }

  if (status === 'loading') {
    return (
      <div className="daily-plan">
        <div className="daily-plan-header">
          <span className="daily-plan-title">Daily Plan</span>
        </div>
        <div className="daily-plan-loading">
          <span className="daily-plan-spinner" />
          <span>Building your plan…</span>
        </div>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="daily-plan daily-plan--error">
        <span className="daily-plan-error-text">{error}</span>
        <button className="daily-plan-retry" onClick={generate}>Retry</button>
      </div>
    )
  }

  const scheduled   = blocks.filter(b => b.time)
  const unscheduled = blocks.filter(b => !b.time && b.type !== 'break')

  return (
    <div className="daily-plan">
      <div className="daily-plan-header">
        <span className="daily-plan-title">Daily Plan</span>
        <button className="daily-plan-regen" onClick={generate} title="Regenerate plan">
          <UpdateIcon />
        </button>
      </div>

      {scheduled.length === 0 && unscheduled.length === 0 ? (
        <p className="daily-plan-empty">Nothing to schedule today.</p>
      ) : (
        <>
          <div className="daily-plan-blocks">
            {scheduled.map((block, i) => {
              const color = TYPE_COLOR[block.type] || '#6b7280'
              const isBreak = block.type === 'break'
              return (
                <div key={i} className={`plan-block${isBreak ? ' plan-block--break' : ''}`}>
                  <span className="plan-block-time">{fmt12h(block.time)}</span>
                  <span className="plan-block-bar" style={{ background: color }} />
                  <div className="plan-block-body">
                    {isBreak ? (
                      <span className="plan-block-break-label">Break · {fmtDuration(block.duration)}</span>
                    ) : (
                      <>
                        <span className="plan-block-title">{block.title}</span>
                        <span className="plan-block-meta">
                          <span className="plan-block-duration">{fmtDuration(block.duration)}</span>
                          {block.note && (
                            <span className="plan-block-note">· {block.note}</span>
                          )}
                        </span>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          {unscheduled.length > 0 && (
            <div className="daily-plan-overflow">
              <span className="daily-plan-overflow-label">Didn't fit today</span>
              <div className="daily-plan-overflow-items">
                {unscheduled.map((b, i) => (
                  <span key={i} className="daily-plan-overflow-item">{b.title}</span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
