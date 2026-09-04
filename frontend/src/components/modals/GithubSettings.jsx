import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import {
  fetchEngineeringConfig, saveEngineeringConfig, syncEngineering,
  fetchStatusConfig, saveStatusConfig, fetchRepoTagsConfig, saveRepoTagsConfig,
  fetchBridgeInstallToken, rotateBridgeInstallToken,
  fetchCheckpointPatterns, saveCheckpointPatterns,
} from '../../api'
import Modal from './Modal'
import './GithubSettings.css'

export default function GithubSettings({ allTags = [], onClose, onSynced }) {
  const [copiedInstall, setCopiedInstall] = useState(false)
  const [bridgeInstallToken, setBridgeInstallToken] = useState('')
  const [rotatingBridgeToken, setRotatingBridgeToken] = useState(false)
  const [token, setToken] = useState('')
  const [repos, setRepos] = useState('')
  const [statusConfig, setStatusConfig] = useState({})
  const [repoTags, setRepoTags] = useState([]) // [{ pattern, tagIds: number[] }]
  const [checkpointPatterns, setCheckpointPatterns] = useState('')
  const [configured, setConfigured] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState(null)
  const [error, setError] = useState('')
  const [savingCheckpoints, setSavingCheckpoints] = useState(false)
  const [checkpointsSaved, setCheckpointsSaved] = useState(false)

  useEffect(() => {
    fetchEngineeringConfig()
      .then((cfg) => {
        setConfigured(cfg.configured)
        setRepos(cfg.repos.join('\n'))
        return Promise.all([
          fetchStatusConfig().then(setStatusConfig).catch(() => {}),
          fetchRepoTagsConfig()
            .then((cfg) => setRepoTags(Object.entries(cfg).map(([pattern, tagIds]) => ({ pattern, tagIds }))))
            .catch(() => {}),
        ])
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
    fetchBridgeInstallToken().then(setBridgeInstallToken).catch(() => {})
    fetchCheckpointPatterns()
      .then((cfg) => setCheckpointPatterns((cfg.patterns || []).join('\n')))
      .catch(() => {})
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setError('')
    setSyncResult(null)
    try {
      const repoList = repos.split('\n').map((r) => r.trim()).filter(Boolean)
      const repoTagsPayload = Object.fromEntries(
        repoTags
          .map((r) => ({ pattern: r.pattern.trim(), tagIds: r.tagIds }))
          .filter((r) => r.pattern)
          .map((r) => [r.pattern, r.tagIds])
      )
      await Promise.all([
        saveEngineeringConfig({ token: token.trim(), repos: repoList }),
        saveStatusConfig(statusConfig),
        saveRepoTagsConfig(repoTagsPayload),
      ])
      const result = await syncEngineering()
      setSyncResult(result)
      if ((result.created > 0 || result.closed > 0) && onSynced) onSynced()
      if (!result.error) setConfigured(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const toggleRepoTag = (i, tagId) => {
    setRepoTags((prev) => prev.map((r, idx) => {
      if (idx !== i) return r
      const has = r.tagIds.includes(tagId)
      return { ...r, tagIds: has ? r.tagIds.filter((id) => id !== tagId) : [...r.tagIds, tagId] }
    }))
  }

  const handleSync = async () => {
    setSyncing(true)
    setError('')
    setSyncResult(null)
    try {
      const result = await syncEngineering()
      setSyncResult(result)
      if ((result.created > 0 || result.closed > 0) && onSynced) onSynced()
    } catch (e) {
      setError(e.message)
    } finally {
      setSyncing(false)
    }
  }

  const handleSaveCheckpoints = async () => {
    setSavingCheckpoints(true)
    setError('')
    try {
      const patterns = checkpointPatterns.split('\n').map((p) => p.trim()).filter(Boolean)
      await saveCheckpointPatterns(patterns)
      setCheckpointsSaved(true)
      setTimeout(() => setCheckpointsSaved(false), 2000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingCheckpoints(false)
    }
  }

  const syncSummary = () => {
    if (!syncResult) return null
    if (syncResult.error) return <span className="gh-sync-error">{syncResult.error}</span>
    const parts = []
    if (syncResult.created > 0) parts.push(`${syncResult.created} new`)
    if (syncResult.closed > 0) parts.push(`${syncResult.closed} closed`)
    if (syncResult.cards_created > 0) parts.push(`${syncResult.cards_created} card${syncResult.cards_created === 1 ? '' : 's'} added to board`)
    if (parts.length === 0) parts.push('Already up to date')
    return <span className="gh-sync-ok">{parts.join(', ')}</span>
  }

  return (
    <Modal onClose={onClose} className="modal--md gh-settings-modal">
      <Dialog.Title asChild><h2>GitHub</h2></Dialog.Title>
      <p className="gh-hint">
        Syncs issues assigned to you and PRs requesting your review into the Engineering page.
        Items are read-only — GitHub is the source of truth.
      </p>

      {loading && <p className="gh-loading">Loading…</p>}

      {!loading && (
        <>
          <div className="gh-field">
            <label className="gh-label">
              Personal access token
              {configured && !token && (
                <span className="gh-configured-badge">configured</span>
              )}
            </label>
            <input
              type="password"
              className="gh-input"
              placeholder={configured ? 'Enter new token to replace' : 'ghp_…'}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <p className="gh-hint gh-hint--small">
              Generate at GitHub → Settings → Developer settings → Personal access tokens.
              Required scopes: <code>repo</code> (or <code>public_repo</code> for public repos only), <code>read:project</code> for board status.
            </p>
          </div>

          <div className="gh-field">
            <label className="gh-label">Repositories to watch <span className="gh-optional">(optional)</span></label>
            <textarea
              className="gh-repos-input"
              placeholder={'owner/repo\nowner/another-repo'}
              value={repos}
              onChange={(e) => setRepos(e.target.value)}
              rows={4}
              spellCheck={false}
            />
            <p className="gh-hint gh-hint--small">
              One <code>owner/repo</code> per line. Leave blank to watch all repos you have access to.
            </p>
          </div>

          <div className="gh-field">
            <label className="gh-label">
              Project board columns <span className="gh-optional">(optional)</span>
            </label>
            <p className="gh-hint gh-hint--small">
              Column names that trigger auto card creation and completion. Leave blank to use defaults ("In Progress" / "Done").
            </p>
            <div className="gh-status-table">
              <div className="gh-status-header">
                <span>Repo</span>
                <span>In Progress column</span>
                <span>Done column</span>
              </div>
              {[{ key: 'default', label: 'Default' }, ...repos.split('\n').map((r) => r.trim()).filter(Boolean).map((r) => ({ key: r, label: r }))].map(({ key, label }) => (
                <div className="gh-status-row" key={key}>
                  <span className="gh-status-repo">{label}</span>
                  <input
                    className="gh-status-input"
                    placeholder="In Progress"
                    value={(statusConfig[key] || {}).in_progress || ''}
                    onChange={(e) => setStatusConfig((prev) => ({ ...prev, [key]: { ...(prev[key] || {}), in_progress: e.target.value } }))}
                  />
                  <input
                    className="gh-status-input"
                    placeholder="Done"
                    value={(statusConfig[key] || {}).done || ''}
                    onChange={(e) => setStatusConfig((prev) => ({ ...prev, [key]: { ...(prev[key] || {}), done: e.target.value } }))}
                  />
                </div>
              ))}
            </div>
          </div>

          <div className="gh-field">
            <label className="gh-label">
              Repo tags <span className="gh-optional">(optional)</span>
            </label>
            <p className="gh-hint gh-hint--small">
              Automatically tag cards created from GitHub issues/PRs. Use <code>owner</code> to match
              every repo under that user or org, or <code>owner/repo</code> for a single repo.
            </p>
            {allTags.length === 0 ? (
              <p className="gh-hint gh-hint--small">Create a tag first to use this.</p>
            ) : (
              <div className="gh-repo-tags-list">
                {repoTags.map((rule, i) => (
                  <div className="gh-repo-tags-row" key={i}>
                    <input
                      className="gh-status-input gh-repo-tags-pattern"
                      placeholder="owner or owner/repo"
                      value={rule.pattern}
                      onChange={(e) => setRepoTags((prev) => prev.map((r, idx) => idx === i ? { ...r, pattern: e.target.value } : r))}
                      spellCheck={false}
                    />
                    <div className="gh-repo-tags-chips">
                      {allTags.map((tag) => {
                        const active = rule.tagIds.includes(tag.id)
                        return (
                          <button
                            type="button"
                            key={tag.id}
                            className={`gh-repo-tag-chip${active ? ' gh-repo-tag-chip--active' : ''}`}
                            style={active
                              ? { background: tag.color, borderColor: tag.color }
                              : { borderColor: tag.color, color: tag.color }}
                            onClick={() => toggleRepoTag(i, tag.id)}
                          >
                            {tag.name}
                          </button>
                        )
                      })}
                    </div>
                    <button
                      type="button"
                      className="gh-repo-tags-remove"
                      onClick={() => setRepoTags((prev) => prev.filter((_, idx) => idx !== i))}
                      aria-label="Remove rule"
                      title="Remove rule"
                    >×</button>
                  </div>
                ))}
                <button
                  type="button"
                  className="gh-add-rule-btn"
                  onClick={() => setRepoTags((prev) => [...prev, { pattern: '', tagIds: [] }])}
                >
                  + Add rule
                </button>
              </div>
            )}
          </div>

          {configured && (
            <div className="gh-sync-row">
              <button
                type="button"
                className="gh-sync-btn"
                onClick={handleSync}
                disabled={syncing}
              >
                {syncing ? 'Syncing…' : 'Sync now'}
              </button>
              {syncSummary()}
            </div>
          )}
        </>
      )}

      {error && <p className="form-error">{error}</p>}

      <div className="gh-bridge-section">
        <div className="gh-label">Claude Code Bridge</div>
        <p className="gh-hint gh-hint--small">
          Run a local agent that picks up build jobs queued from this app and launches Claude Code automatically.
        </p>
        <div className="gh-install-row">
          <code className="gh-install-cmd">
            {`curl "${window.location.origin}/api/bridge/install.py?token=${bridgeInstallToken}" | python3`}
          </code>
          <button
            className="gh-install-copy"
            disabled={!bridgeInstallToken}
            onClick={() => {
              navigator.clipboard.writeText(`curl "${window.location.origin}/api/bridge/install.py?token=${bridgeInstallToken}" | python3`)
              setCopiedInstall(true)
              setTimeout(() => setCopiedInstall(false), 2000)
            }}
          >
            {copiedInstall ? '✓' : 'Copy'}
          </button>
        </div>
        <p className="gh-hint gh-hint--small">
          Anyone with this command can install the bridge and access this app.{' '}
          <button
            type="button"
            className="gh-rotate-link"
            disabled={rotatingBridgeToken}
            onClick={async () => {
              if (!window.confirm("Rotate the bridge install token? The command above will change; any copy of it you've saved elsewhere will stop working.")) return
              setRotatingBridgeToken(true)
              try { setBridgeInstallToken(await rotateBridgeInstallToken()) } catch { /* ignore */ }
              setRotatingBridgeToken(false)
            }}
          >
            {rotatingBridgeToken ? 'Rotating…' : 'Rotate token'}
          </button>
        </p>
        <p className="gh-hint gh-hint--small">
          After installing, run <code>qtask-bridge --watch</code> in your project directory.
          Open any card, generate a spec, then click <strong>▶ Bridge</strong> to queue a job.
        </p>

        <div className="gh-field">
          <label className="gh-label">
            Checkpoint patterns <span className="gh-optional">(optional)</span>
          </label>
          <p className="gh-hint gh-hint--small">
            If an unattended job's changes touch any of these paths, it's marked "Needs
            confirmation" instead of "Done" so it doesn't get mistaken for fully resolved.
          </p>
          <textarea
            className="gh-repos-input"
            placeholder={'alembic/versions/*\npackage.json'}
            value={checkpointPatterns}
            onChange={(e) => setCheckpointPatterns(e.target.value)}
            rows={3}
            spellCheck={false}
          />
          <div className="gh-sync-row">
            <button
              type="button"
              className="gh-sync-btn"
              onClick={handleSaveCheckpoints}
              disabled={savingCheckpoints}
            >
              {savingCheckpoints ? 'Saving…' : 'Save patterns'}
            </button>
            {checkpointsSaved && <span className="gh-sync-ok">Saved</span>}
          </div>
        </div>
      </div>

      <div className="modal-footer">
        <button className="btn-cancel" onClick={onClose}>Cancel</button>
        <button
          className="btn-save"
          onClick={handleSave}
          disabled={saving || loading || (!token.trim() && !configured)}
        >
          {saving ? 'Saving…' : 'Save & Sync'}
        </button>
      </div>
    </Modal>
  )
}
