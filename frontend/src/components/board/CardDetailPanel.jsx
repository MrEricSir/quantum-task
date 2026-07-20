import { useState, useEffect } from 'react'
import { Cross2Icon, CopyIcon, CheckIcon } from '@radix-ui/react-icons'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import AssistModal from '../modals/AssistModal'
import CardForm, { isoToLocal } from '../modals/CardForm'
import { SECTIONS, SECTION_LABELS } from '../../lib/sections'
import descriptionToHtml from '../../lib/descriptionToHtml'
import './CardDetailPanel.css'

function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text, { breaks: true }), { ADD_ATTR: ['target', 'rel'] })
}

const SECTION_COLORS = {
  today: 'var(--color-today)',
  week:  'var(--color-week)',
  month: 'var(--color-month)',
  later: 'var(--color-later)',
}

function parseGitHubUrl(str) {
  if (!str) return null
  const m = str.match(/^https:\/\/github\.com\/([^/]+\/[^/]+)\/(pull|issues?)\/(\d+)/)
  if (!m) return null
  return { repo: m[1], type: m[2].startsWith('pull') ? 'PR' : 'Issue', number: m[3], url: str }
}

export default function CardDetailPanel({
  card,
  initialMode = 'view',
  defaultSection = 'today',
  allTags = [],
  topTags = [],
  engineeringItems = [],
  onClose,
  onCreate,
  onSave,
  onCreateTag,
  onToggle,
  onMove,
  onDelete,
  onArchive,
  onEdit,    // kept for external callers (e.g. Archive)
  onBreakdown,
  onRefreshGithubItem,
}) {
  const [mode, setMode] = useState(initialMode)

  // ── Saved output (view + assist) ──────────────────────────────────────────
  const [savedOutput,   setSavedOutput]   = useState(card?.thread_output ?? null)
  const [copiedOutput,  setCopiedOutput]  = useState(false)

  // ── Edit / new form state ─────────────────────────────────────────────────
  const [editTitle,          setEditTitle]          = useState('')
  const [editDescription,    setEditDescription]    = useState('')
  const [editSection,        setEditSection]        = useState(defaultSection)
  const [editScheduledAt,    setEditScheduledAt]    = useState('')
  const [editRecurrenceRule, setEditRecurrenceRule] = useState('')
  const [editTags,           setEditTags]           = useState([])
  const [editError,          setEditError]          = useState('')
  const [editSaving,         setEditSaving]         = useState(false)

  // ── Description expand ───────────────────────────────────────────────────
  const [showFullDesc, setShowFullDesc] = useState(false)

  // ── GitHub context panel ──────────────────────────────────────────────────
  const [ghExpanded,  setGhExpanded]  = useState(true)
  const [ghRefreshing, setGhRefreshing] = useState(false)

  // ── Reset when a different card is opened or initialMode changes ──────────
  useEffect(() => {
    setMode(initialMode)
    setSavedOutput(card?.thread_output ?? null)
    setShowFullDesc(false)
    setGhExpanded(true)
    setGhRefreshing(false)
    setEditError('')
  }, [card?.id, initialMode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Populate edit form fields whenever entering edit/new mode
  useEffect(() => {
    if (mode === 'edit' || mode === 'new') {
      setEditTitle(card?.title ?? '')
      setEditDescription(card?.description ?? '')
      setEditSection(card?.section ?? defaultSection)
      setEditScheduledAt(card?.scheduled_at ? isoToLocal(card.scheduled_at) : '')
      setEditRecurrenceRule(card?.recurrence_rule ?? '')
      setEditTags(card?.tags ?? [])
      setEditError('')
    }
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Close on Escape
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      if (mode === 'assist') { setMode('view'); return }
      if (mode === 'edit' || mode === 'new') { setMode(card ? 'view' : null); if (!card) onClose(); return }
      onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [mode, card, onClose])

  // ── Edit / new form submit ────────────────────────────────────────────────
  const handleFormSubmit = async (e) => {
    e.preventDefault()
    const resolvedTitle = editTitle.trim()
    if (!resolvedTitle) { setEditError('Title is required.'); return }
    setEditSaving(true)
    try {
      const resolvedTags = []
      for (const tag of editTags) {
        if (tag.id) {
          resolvedTags.push(tag)
        } else if (onCreateTag) {
          const created = await onCreateTag({ name: tag.name, color: tag.color, is_project: false })
          if (created) resolvedTags.push(created)
        }
      }
      const data = {
        title: resolvedTitle,
        description: editDescription.trim() || null,
        section: editSection,
        scheduled_at: editScheduledAt || null,
        recurrence_rule: editRecurrenceRule || null,
        tag_ids: resolvedTags.map(t => t.id),
      }
      if (mode === 'new') {
        await onCreate?.(data)
        onClose()
      } else {
        await onSave?.(card.id, data)
        setMode('view')
      }
    } catch {
      setEditError('Something went wrong. Please try again.')
      setEditSaving(false)
    }
  }

  const handleDelete = () => {
    if (window.confirm(`Delete "${card?.title}"? This cannot be undone.`)) {
      onDelete?.(card.id)
      onClose()
    }
  }


  const isNew = mode === 'new'

  // Find linked engineering item (GitHub issue/PR) by external_id
  const engItem = card?.external_id
    ? engineeringItems.find(i => i.external_id === card.external_id) ?? null
    : null

  // Old-style GitHub badge: only for cards without an engItem match where description is a bare URL
  const gh = !engItem && card ? parseGitHubUrl(card.description) : null

  const handleGhRefresh = async () => {
    if (!engItem || ghRefreshing) return
    setGhRefreshing(true)
    try {
      await onRefreshGithubItem?.(engItem.id)
    } finally {
      setGhRefreshing(false)
    }
  }

  // ── Header ────────────────────────────────────────────────────────────────
  const headerTitle =
    mode === 'assist' ? 'Assistant' :
    mode === 'edit'   ? 'Edit card' :
    mode === 'new'    ? 'New card'  :
    card?.title ?? ''

  return (
    <>
      {/* Backdrop — click to close */}
      <div className="cdp-backdrop" onClick={onClose} />

      <aside className="card-detail-panel" aria-label="Card detail">
        {/* Header */}
        <div className="cdp-header">
          {mode === 'view' && card && (
            <span
              className="cdp-section-dot"
              style={{ background: SECTION_COLORS[card.section] ?? '#6b7280' }}
              title={SECTION_LABELS[card.section]}
            />
          )}
          <span className="cdp-title">{headerTitle}</span>
          {card?.id && (
            <span className="cdp-card-id">#{card.id}</span>
          )}
          <button className="cdp-close" onClick={onClose} aria-label="Close panel">
            <Cross2Icon />
          </button>
        </div>

        {/* ── VIEW mode ── */}
        {mode === 'view' && card && (
          <>
            <div className="cdp-body">

              {/* Metadata */}
              {(card.scheduled_at || card.recurrence_rule || card.waiting_reason) && (
                <div className="cdp-meta">
                  {card.scheduled_at && (
                    <span className="cdp-meta-item">
                      🕐 {new Date(card.scheduled_at).toLocaleString(undefined, {
                        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
                      })}
                    </span>
                  )}
                  {card.recurrence_rule && (
                    <span className="cdp-meta-item cdp-meta-item--recur">↻ {card.recurrence_rule}</span>
                  )}
                  {card.waiting_reason && (
                    <span className="cdp-meta-item cdp-meta-item--waiting">⏳ Waiting: {card.waiting_reason}</span>
                  )}
                </div>
              )}

              {/* GitHub Issue / PR context */}
              {engItem && (
                <div className="cdp-section">
                  <div className="cdp-gh-header">
                    <span className={`cdp-gh-type cdp-gh-type--${engItem.item_type}`}>
                      {engItem.item_type === 'pr' ? 'PR' : 'Issue'}
                    </span>
                    <a
                      href={engItem.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="cdp-gh-link"
                    >
                      {engItem.repo}#{engItem.number} ↗
                    </a>
                    <div className="cdp-gh-actions">
                      <button
                        className="cdp-gh-btn"
                        onClick={handleGhRefresh}
                        disabled={ghRefreshing}
                        title="Refresh from GitHub"
                      >
                        {ghRefreshing ? '…' : '↻ Refresh'}
                      </button>
                      <button
                        className="cdp-gh-btn"
                        onClick={() => setGhExpanded(v => !v)}
                        title={ghExpanded ? 'Collapse' : 'Expand'}
                      >
                        {ghExpanded ? '▲' : '▼'}
                      </button>
                    </div>
                  </div>

                  {ghExpanded && (
                    <div className="cdp-gh-content">
                      {engItem.body ? (
                        <div
                          className="cdp-gh-markdown"
                          dangerouslySetInnerHTML={{ __html: renderMarkdown(engItem.body) }}
                        />
                      ) : (
                        <div className="cdp-gh-empty">No description.</div>
                      )}

                      {(engItem.comments ?? []).length > 0 && (
                        <div className="cdp-gh-comments">
                          {(engItem.comments ?? []).map(c => (
                            <div key={c.id} className="cdp-gh-comment">
                              <div className="cdp-gh-comment-meta">
                                <span className="cdp-gh-comment-author">{c.author}</span>
                                <span className="cdp-gh-comment-date">
                                  {new Date(c.created_at).toLocaleDateString(undefined, {
                                    month: 'short', day: 'numeric', year: 'numeric',
                                  })}
                                </span>
                              </div>
                              <div
                                className="cdp-gh-markdown"
                                dangerouslySetInnerHTML={{ __html: renderMarkdown(c.body) }}
                              />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Description */}
              {card.description && (
                <div className="cdp-section">
                  <div className="cdp-section-label">Notes</div>
                  {gh ? (
                    <a href={gh.url} target="_blank" rel="noopener noreferrer" className="cdp-github-badge">
                      {gh.repo} #{gh.number} ↗
                    </a>
                  ) : (
                    <>
                      <div
                        className={`cdp-description${showFullDesc ? ' cdp-description--expanded' : ''}`}
                        dangerouslySetInnerHTML={{ __html: descriptionToHtml(card.description) }}
                      />
                      {card.description.length > 200 && (
                        <button className="cdp-desc-toggle" onClick={() => setShowFullDesc(v => !v)}>
                          {showFullDesc ? 'Show less' : 'Show more'}
                        </button>
                      )}
                    </>
                  )}
                </div>
              )}

              {/* AI output */}
              {savedOutput && (
                <div className="cdp-section">
                  <div className="cdp-output-header">
                    <div className="cdp-section-label">✦ Assistant output</div>
                    <button
                      className="cdp-copy-btn"
                      onClick={() => navigator.clipboard.writeText(savedOutput).then(() => { setCopiedOutput(true); setTimeout(() => setCopiedOutput(false), 2000) })}
                      title="Copy"
                    >
                      {copiedOutput ? <CheckIcon /> : <CopyIcon />}
                    </button>
                  </div>
                  <div className="cdp-output-text">{savedOutput}</div>
                </div>
              )}

              {/* Tags */}
              {(card.tags ?? []).length > 0 && (
                <div className="cdp-section">
                  <div className="cdp-section-label">Tags</div>
                  <div className="cdp-tags">
                    {(card.tags ?? []).map((tag) => (
                      <span key={tag.id} className="cdp-tag" style={{ background: tag.color }}>
                        {tag.name}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Move to */}
              <div className="cdp-section">
                <div className="cdp-section-label">Move to</div>
                <div className="cdp-move-btns">
                  {SECTIONS.filter(s => s !== card.section).map((s) => (
                    <button
                      key={s}
                      className="cdp-move-btn"
                      style={{ borderColor: SECTION_COLORS[s], color: SECTION_COLORS[s] }}
                      onClick={() => { onMove?.(card.id, s); onClose() }}
                    >
                      {SECTION_LABELS[s]}
                    </button>
                  ))}
                </div>
              </div>

            </div>

            {/* Footer — fixed at bottom, matches mobile sheet */}
            <div className="cdp-footer">
              <button
                className={`cdp-btn cdp-btn--toggle${card.completed ? ' cdp-btn--done' : ''}`}
                onClick={() => onToggle?.(card)}
              >
                {card.completed ? 'Undo' : 'Complete'}
              </button>
              {onArchive && (
                <button className="cdp-btn cdp-btn--secondary" onClick={() => { onArchive(card.id); onClose() }}>
                  Archive
                </button>
              )}
              <button className="cdp-btn cdp-btn--assist-footer" onClick={() => setMode('assist')}>
                ✦ {savedOutput ? 'Assistant' : 'Assist'}
              </button>
              <button className="cdp-btn cdp-btn--edit" onClick={() => setMode('edit')}>
                Edit
              </button>
            </div>
          </>
        )}

        {/* ── EDIT / NEW mode ── */}
        {(mode === 'edit' || mode === 'new') && (
          <form className="cdp-form" onSubmit={handleFormSubmit} noValidate>
            <div className="cdp-body">
              <CardForm
                idPrefix="cdp"
                title={editTitle}
                setTitle={(v) => { setEditTitle(v); setEditError('') }}
                description={editDescription}
                setDescription={setEditDescription}
                section={editSection}
                setSection={setEditSection}
                scheduledAt={editScheduledAt}
                setScheduledAt={setEditScheduledAt}
                recurrenceRule={editRecurrenceRule}
                setRecurrenceRule={setEditRecurrenceRule}
                allTags={allTags}
                topTags={topTags}
                selectedTags={editTags}
                onSelectedTagsChange={setEditTags}
                titleError={editError}
                autoFocus
              />
            </div>
            <div className="cdp-form-footer">
              {!isNew && onArchive && (
                <button type="button" className="cdp-btn cdp-btn--secondary" onClick={() => { onArchive(card.id); onClose() }}>
                  Archive
                </button>
              )}
              <button type="button" className="cdp-btn cdp-btn--cancel" onClick={() => isNew ? onClose() : setMode('view')}>
                Cancel
              </button>
              <button type="submit" className="cdp-btn cdp-btn--save" disabled={editSaving}>
                {editSaving ? 'Saving…' : isNew ? 'Add card' : 'Save'}
              </button>
            </div>
          </form>
        )}

        {/* ── ASSIST mode ── */}
        {mode === 'assist' && card && (
          <AssistModal
            open
            onClose={() => setMode('view')}
            task={card}
            onBreakdown={onBreakdown}
            onOutputSaved={(output) => setSavedOutput(output)}
            onSpecSaved={(spec) => onSave?.(card.id, { spec })}
            inline
          />
        )}
      </aside>
    </>
  )
}
