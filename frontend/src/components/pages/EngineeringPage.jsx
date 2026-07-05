import { UpdateIcon } from '@radix-ui/react-icons'
import { useModalContext } from '../../context/ModalContext'
import './EngineeringPage.css'

function formatSynced(date) {
  if (!date) return null
  const diffMin = Math.floor((Date.now() - date.getTime()) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin === 1) return '1 min ago'
  return `${diffMin} min ago`
}

function ItemCard({ item, onAddToBoard, isAdded }) {
  return (
    <div className="eng-item">
      <a
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        className="eng-item-link"
      >
        <span className={`eng-item-type eng-item-type--${item.item_type}`}>
          {item.item_type === 'pr' ? 'PR' : 'Issue'}
        </span>
        <span className="eng-item-title">{item.title}</span>
        <span className="eng-item-meta">{item.repo}#{item.number} ↗</span>
      </a>
      <button
        type="button"
        className={`eng-add-btn${isAdded ? ' eng-add-btn--added' : ''}`}
        onClick={() => onAddToBoard(item)}
        disabled={isAdded}
        aria-label="Add to board"
        title={isAdded ? 'Already on board' : 'Add to board'}
      >
        {isAdded ? '✓' : '+ Board'}
      </button>
    </div>
  )
}

export default function EngineeringPage({ items, todos = [], lastSynced, syncing, onSync, onAddToBoard }) {
  const { openGithubSettings } = useModalContext()
  const prs    = items.filter((i) => i.item_type === 'pr')
  const issues = items.filter((i) => i.item_type === 'issue')
  const noConfig = items.length === 0 && !syncing

  const isAdded = (item) => todos.some(
    (t) => (t.external_id && t.external_id === item.external_id) || t.description === item.url
  )

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
          <button className="eng-configure-link" onClick={openGithubSettings}>
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
            {prs.map((item) => <ItemCard key={item.id} item={item} onAddToBoard={onAddToBoard} isAdded={isAdded(item)} />)}
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
            {issues.map((item) => <ItemCard key={item.id} item={item} onAddToBoard={onAddToBoard} isAdded={isAdded(item)} />)}
          </div>
        </section>
      )}
    </div>
  )
}
