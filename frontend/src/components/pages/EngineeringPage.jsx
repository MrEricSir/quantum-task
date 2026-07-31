import { UpdateIcon } from '@radix-ui/react-icons'
import { useModalContext } from '../../context/ModalContext'
import './EngineeringPage.css'

// Cap tags shown per row so one item with several matching repo-tag rules
// doesn't widen the tags column for every other row in the list.
const MAX_VISIBLE_TAGS = 2

function formatSynced(date) {
  if (!date) return null
  const diffMin = Math.floor((Date.now() - date.getTime()) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin === 1) return '1 min ago'
  return `${diffMin} min ago`
}

function ItemCard({ item, onAddToBoard, onOpenCard, addedCard }) {
  const isAdded = !!addedCard
  const tags = item.tags ?? []
  const visibleTags = tags.slice(0, MAX_VISIBLE_TAGS)
  const overflowTags = tags.slice(MAX_VISIBLE_TAGS)

  return (
    <div className="eng-item">
      {/* display:contents on the link lets title/status/sub participate
          directly as grid areas in the parent grid, while keeping the whole
          row clickable as a single link to GitHub. */}
      <a
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        className="eng-item-link"
      >
        <span className="eng-item-title">{item.title}</span>
        {/* Always rendered (even when empty) so the status column lines up
            across rows regardless of which items have a board status. */}
        <span className="eng-item-status">
          {item.project_status && (
            <span className="eng-item-status-pill" title={item.project_name || undefined}>
              {item.project_status}
            </span>
          )}
        </span>
        {/* Second line: tags + repo/issue link, kept off the title line. */}
        <span className="eng-item-sub">
          <span className="eng-item-tags">
            {visibleTags.map((tag) => (
              <span key={tag.id} className="eng-item-tag" style={{ background: tag.color }}>
                {tag.name}
              </span>
            ))}
            {overflowTags.length > 0 && (
              <span
                className="eng-item-tag eng-item-tag--overflow"
                title={overflowTags.map((t) => t.name).join(', ')}
              >
                +{overflowTags.length}
              </span>
            )}
          </span>
          <span className="eng-item-meta">{item.repo}#{item.number} ↗</span>
        </span>
      </a>
      <button
        type="button"
        className={`eng-add-btn${isAdded ? ' eng-add-btn--added' : ''}`}
        onClick={() => (isAdded ? onOpenCard(addedCard) : onAddToBoard(item))}
        aria-label={isAdded ? 'Open card on board' : 'Add to board'}
        title={isAdded ? 'Open card on board' : 'Add to board'}
      >
        {isAdded ? '✓' : '+ Board'}
      </button>
    </div>
  )
}

export default function EngineeringPage({ items, cards = [], lastSynced, syncing, onSync, onAddToBoard, onOpenCard }) {
  const { openGithubSettings } = useModalContext()
  const prs    = items.filter((i) => i.item_type === 'pr')
  const issues = items.filter((i) => i.item_type === 'issue')
  const noConfig = items.length === 0 && !syncing

  const findAddedCard = (item) => cards.find(
    (t) => (t.external_id && t.external_id === item.external_id) || t.description === item.url
  ) ?? null

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
            {prs.map((item) => <ItemCard key={item.id} item={item} onAddToBoard={onAddToBoard} onOpenCard={onOpenCard} addedCard={findAddedCard(item)} />)}
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
            {issues.map((item) => <ItemCard key={item.id} item={item} onAddToBoard={onAddToBoard} onOpenCard={onOpenCard} addedCard={findAddedCard(item)} />)}
          </div>
        </section>
      )}
    </div>
  )
}
