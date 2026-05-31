import { useState, useEffect, useRef } from 'react'
import { Cross2Icon, ExclamationTriangleIcon, ReloadIcon } from '@radix-ui/react-icons'
import './QueueIndicator.css'

export default function QueueIndicator({ items, onDismiss, onRetry, onClearErrors }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  const pending = items.filter((i) => i.status === 'pending')
  const errors  = items.filter((i) => i.status === 'error')

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  if (items.length === 0) return null

  const hasErrors  = errors.length > 0
  const hasPending = pending.length > 0

  return (
    <div className="qi-wrap" ref={ref}>
      <button
        className={`qi-btn ${hasErrors && !hasPending ? 'qi-btn--error' : ''}`}
        onClick={() => setOpen((o) => !o)}
        title="Processing queue"
        aria-label="Processing queue"
      >
        {hasPending ? <span className="qi-spinner" /> : <ExclamationTriangleIcon />}
        <span className="qi-count">{items.length}</span>
      </button>

      {open && (
        <div className="qi-panel">
          <div className="qi-panel-header">
            <span className="qi-panel-title">Queue</span>
            {hasErrors && !hasPending && (
              <button className="qi-clear" onClick={() => { onClearErrors(); setOpen(false) }}>
                Clear all
              </button>
            )}
          </div>
          <div className="qi-items">
            {items.map((item) => (
              <div key={item.id} className={`qi-item qi-item--${item.status}`}>
                <span className="qi-item-icon">
                  {item.status === 'pending' && <span className="qi-spinner-sm" />}
                  {item.status === 'error'   && <ExclamationTriangleIcon />}
                </span>
                <div className="qi-item-body">
                  <span className="qi-item-text">{item.text}</span>
                  {item.status === 'error' && (
                    <span className="qi-item-error">{item.errorMsg}</span>
                  )}
                </div>
                <div className="qi-item-actions">
                  {item.status === 'error' && (
                    <button
                      className="qi-item-retry"
                      onClick={() => onRetry(item.id, item.text)}
                      title="Retry"
                      aria-label="Retry"
                    >
                      <ReloadIcon />
                    </button>
                  )}
                  <button
                    className="qi-item-dismiss"
                    onClick={() => onDismiss(item.id)}
                    disabled={item.status === 'pending'}
                    title="Dismiss"
                    aria-label="Dismiss"
                  >
                    <Cross2Icon />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
