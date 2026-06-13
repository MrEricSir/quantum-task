import { UpdateIcon } from '@radix-ui/react-icons'
import './EngineeringPage.css'

function formatSynced(date) {
  if (!date) return null
  const diffMin = Math.floor((Date.now() - date.getTime()) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin === 1) return '1 min ago'
  return `${diffMin} min ago`
}

function ItemCard({ item }) {
  return (
    <a
      href={item.url}
      target="_blank"
      rel="noopener noreferrer"
      className="eng-item"
    >
      <span className={`eng-item-type eng-item-type--${item.item_type}`}>
        {item.item_type === 'pr' ? 'PR' : 'Issue'}
      </span>
      <span className="eng-item-title">{item.title}</span>
      <span className="eng-item-meta">{item.repo}#{item.number} ↗</span>
    </a>
  )
}

export default function EngineeringPage({ items, lastSynced, syncing, onSync, onOpenSettings }) {
  const prs    = items.filter((i) => i.item_type === 'pr')
  const issues = items.filter((i) => i.item_type === 'issue')
  const noConfig = items.length === 0 && !syncing

  return (
    <div className="eng-page">
      <div className="eng-toolbar">
        <div className="eng-meta">
          {lastSynced && (
            <span className="eng-updated">Synced {formatSynced(lastSynced)}</span>
          )}
        </div>
        <button
          className={`eng-sync-btn${syncing ? ' eng-sync-btn--spinning' : ''}`}
          onClick={onSync}
          disabled={syncing}
          title="Sync now"
          aria-label="Sync engineering items"
        >
          <UpdateIcon />
        </button>
      </div>

      {noConfig && (
        <div className="eng-empty">
          No open items.{' '}
          <button className="eng-configure-link" onClick={onOpenSettings}>
            Configure GitHub
          </button>{' '}
          in Settings to get started.
        </div>
      )}

      {prs.length > 0 && (
        <section className="eng-section">
          <h3 className="eng-section-heading">
            PRs to Review
            <span className="eng-count">{prs.length}</span>
          </h3>
          <div className="eng-items">
            {prs.map((item) => <ItemCard key={item.id} item={item} />)}
          </div>
        </section>
      )}

      {issues.length > 0 && (
        <section className="eng-section">
          <h3 className="eng-section-heading">
            Assigned Issues
            <span className="eng-count">{issues.length}</span>
          </h3>
          <div className="eng-items">
            {issues.map((item) => <ItemCard key={item.id} item={item} />)}
          </div>
        </section>
      )}
    </div>
  )
}
