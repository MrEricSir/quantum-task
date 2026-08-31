import { UpdateIcon } from '@radix-ui/react-icons'
import { useEffect, useMemo, useState } from 'react'
import { useModalContext } from '../../context/ModalContext'
import { useBridgeJobsDashboard } from '../../hooks/useBridgeJobsDashboard'
import './EngineeringPage.css'

// Cap tags shown per row so one item with several matching repo-tag rules
// doesn't widen the tags column for every other row in the list.
const MAX_VISIBLE_TAGS = 2

// A PR with no GitHub activity (comments, commits, label changes -- anything
// that bumps GitHub's own `updated_at`) in this many days is flagged stale.
// Issues aren't flagged -- an assigned issue can legitimately sit untouched
// far longer than an open PR waiting on review.
const STALE_DAYS = 7

function formatSynced(date) {
  if (!date) return null
  const diffMin = Math.floor((Date.now() - date.getTime()) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin === 1) return '1 min ago'
  return `${diffMin} min ago`
}

function staleDays(item) {
  if (item.item_type !== 'pr' || !item.body_updated_at) return null
  const days = Math.floor((Date.now() - new Date(item.body_updated_at).getTime()) / 86400000)
  return days >= STALE_DAYS ? days : null
}

// Mirrors CardDetailPanel.css's .cdp-bridge-status--* labels (the Code tab's own per-job
// status text) so the dashboard and the Code tab agree on what each status means.
const BUILD_STATUS_LABELS = {
  pending: 'Queued',
  running: 'Running',
  done: 'Done',
  error: 'Error',
  stalled: 'Stalled',
  blocked: 'Blocked',
}

function formatRelativeTime(iso) {
  if (!iso) return ''
  const diffMin = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  return `${Math.floor(diffHr / 24)}d ago`
}

function BuildRow({ job, card, onOpenCard }) {
  const label = BUILD_STATUS_LABELS[job.status] ?? job.status
  return (
    <div
      className={`eng-build-row${card ? ' eng-build-row--clickable' : ''}`}
      onClick={card ? () => onOpenCard(card) : undefined}
      role={card ? 'button' : undefined}
      tabIndex={card ? 0 : undefined}
    >
      <span className={`eng-build-dot eng-build-dot--${job.status}`} />
      <span className="eng-build-title">{job.card_title}</span>
      <span className={`eng-build-status-pill eng-build-status-pill--${job.status}`}>{label}</span>
      <span className="eng-build-sub">
        {job.branch_name && <span className="eng-build-branch">{job.branch_name}</span>}
        {job.target_repo && <span className="eng-build-repo">{job.target_repo}</span>}
        <span className="eng-build-time">{formatRelativeTime(job.updated_at || job.created_at)}</span>
      </span>
    </div>
  )
}

function ItemCard({ item, onAddToBoard, onOpenCard, addedCard }) {
  const isAdded = !!addedCard
  const tags = item.tags ?? []
  const visibleTags = tags.slice(0, MAX_VISIBLE_TAGS)
  const overflowTags = tags.slice(MAX_VISIBLE_TAGS)
  const stale = staleDays(item)

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
          {stale != null && (
            <span className="eng-item-stale-badge" title={`No activity in ${stale} days`}>
              Stale {stale}d
            </span>
          )}
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
  const [selectedRepo, setSelectedRepo] = useState(null)
  const { jobs: buildJobs } = useBridgeJobsDashboard()

  useEffect(() => {
    onSync()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const repos = useMemo(
    () => [...new Set(items.map((i) => i.repo))].sort(),
    [items]
  )
  // Reset a stale repo selection (e.g. that repo's items all closed/synced
  // away) back to "All" rather than silently showing an empty list.
  useEffect(() => {
    if (selectedRepo && !repos.includes(selectedRepo)) setSelectedRepo(null)
  }, [repos, selectedRepo])

  const visibleItems = selectedRepo ? items.filter((i) => i.repo === selectedRepo) : items
  const prs    = visibleItems.filter((i) => i.item_type === 'pr')
  const issues = visibleItems.filter((i) => i.item_type === 'issue')
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

      {repos.length > 1 && (
        <div className="eng-repo-filter">
          <button
            className={`eng-repo-pill${selectedRepo === null ? ' eng-repo-pill--active' : ''}`}
            onClick={() => setSelectedRepo(null)}
          >
            All
          </button>
          {repos.map((repo) => (
            <button
              key={repo}
              className={`eng-repo-pill${selectedRepo === repo ? ' eng-repo-pill--active' : ''}`}
              onClick={() => setSelectedRepo(repo)}
              title={repo}
            >
              {repo.split('/').pop()}
            </button>
          ))}
        </div>
      )}

      {noConfig && (
        <div className="eng-empty">
          No open items.{' '}
          <button className="eng-configure-link" onClick={openGithubSettings}>
            Configure GitHub
          </button>{' '}
          in Settings to get started.
        </div>
      )}

      {buildJobs.length > 0 && (
        <section className="eng-section">
          <h3 className="eng-section-heading">
            Builds
            <span className="eng-count">{buildJobs.length}</span>
          </h3>
          <div className="eng-items">
            {buildJobs.map((job) => (
              <BuildRow
                key={job.id}
                job={job}
                card={cards.find((c) => c.id === job.card_id) ?? null}
                onOpenCard={onOpenCard}
              />
            ))}
          </div>
        </section>
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
