import { useState, useEffect, useRef, useLayoutEffect } from 'react'
import { UpdateIcon, Cross2Icon } from '@radix-ui/react-icons'
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

function nowHHMM() {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// Props come from useDailyPlan hook in TodayPage
export default function DailyPlan({ status, blocks, isStale, error, onGenerate, onDismiss }) {
  const [currentTime, setCurrentTime] = useState(nowHHMM)
  const innerRef = useRef(null)
  const outerRef = useRef(null)

  // Advance the trim cursor every minute
  useEffect(() => {
    const id = setInterval(() => setCurrentTime(nowHHMM()), 60_000)
    return () => clearInterval(id)
  }, [])

  // Animate container height changes smoothly via ResizeObserver
  useLayoutEffect(() => {
    const inner = innerRef.current
    const outer = outerRef.current
    if (!inner || !outer) return
    outer.style.height = `${inner.offsetHeight}px`
    const obs = new ResizeObserver(() => {
      outer.style.height = `${inner.offsetHeight}px`
    })
    obs.observe(inner)
    return () => obs.disconnect()
  })

  if (status === 'idle') {
    return (
      <div className="daily-plan daily-plan--idle">
        <button className="daily-plan-trigger" onClick={onGenerate}>
          ✦ Plan my day
        </button>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="daily-plan daily-plan--error">
        <span className="daily-plan-error-text">{error}</span>
        <button className="daily-plan-retry" onClick={onGenerate}>Retry</button>
      </div>
    )
  }

  const isLoading = status === 'loading'

  // Upcoming: scheduled blocks that haven't started yet
  const upcoming = blocks.filter(b => b.time && b.time >= currentTime)
  // Overflow: no time (couldn't fit) OR time has already passed — non-break only
  const overflow = blocks.filter(b => b.type !== 'break' && (!b.time || b.time < currentTime))

  return (
    <div className="daily-plan">
      <div className="daily-plan-header">
        <span className="daily-plan-title">
          Daily Plan
          {isStale && <span className="daily-plan-stale-dot" title="Schedule has changed" />}
        </span>
        <div className="daily-plan-header-actions">
          {isLoading && <span className="daily-plan-spinner daily-plan-spinner--inline" title="Scheduling…" />}
          {!isLoading && isStale && (
            <button className="daily-plan-update-btn" onClick={onGenerate}>
              Update
            </button>
          )}
          {!isLoading && (
            <button className="daily-plan-regen" onClick={onGenerate} title="Regenerate plan">
              <UpdateIcon />
            </button>
          )}
          <button className="daily-plan-dismiss" onClick={onDismiss} title="Dismiss plan">
            <Cross2Icon />
          </button>
        </div>
      </div>

      <div ref={outerRef} className="daily-plan-body-outer">
        <div ref={innerRef}>
          {upcoming.length === 0 && overflow.length === 0 ? (
            <p className="daily-plan-empty">All caught up — nothing left today.</p>
          ) : (
            <>
              <div className="daily-plan-blocks">
                {upcoming.map((block) => {
                  const color = TYPE_COLOR[block.type] || '#6b7280'
                  const isBreak = block.type === 'break'
                  const key = isBreak ? `break-${block.time}` : block.title
                  return (
                    <div key={key} className={`plan-block${isBreak ? ' plan-block--break' : ''}`}>
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
                              {block.note && <span className="plan-block-note">· {block.note}</span>}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>

              {!isLoading && overflow.length > 0 && (
                <div className="daily-plan-overflow">
                  <span className="daily-plan-overflow-label">Didn't get to</span>
                  <div className="daily-plan-overflow-items">
                    {overflow.map((b) => (
                      <span key={b.title} className="daily-plan-overflow-item">{b.title}</span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
