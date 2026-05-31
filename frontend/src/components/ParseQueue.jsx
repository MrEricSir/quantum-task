import { useState } from 'react'
import './ParseQueue.css'

export default function ParseQueue({ items, onConfirm, onDismiss }) {
  const [expandedError, setExpandedError] = useState(null)

  if (items.length === 0) return null

  return (
    <div className="parse-queue">
      {items.map((item) => (
        <div key={item.id} className={`pq-item pq-item--${item.status}`}>
          <button
            className="pq-main"
            disabled={item.status === 'pending'}
            onClick={() => {
              if (item.status === 'done') {
                onConfirm(item)
              } else if (item.status === 'error') {
                setExpandedError(expandedError === item.id ? null : item.id)
              }
            }}
          >
            <span className="pq-icon" aria-hidden>
              {item.status === 'pending' && <span className="pq-spinner" />}
              {item.status === 'done' && '✓'}
              {item.status === 'error' && '⚠'}
            </span>
            <span className="pq-text">{item.text}</span>
            {item.status === 'done' && (
              <span className="pq-action-hint">click to review</span>
            )}
          </button>

          {item.status === 'error' && expandedError === item.id && (
            <div className="pq-error-msg">{item.errorMsg}</div>
          )}

          <button
            className="pq-dismiss"
            onClick={() => {
              if (expandedError === item.id) setExpandedError(null)
              onDismiss(item.id)
            }}
            title="Dismiss"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
