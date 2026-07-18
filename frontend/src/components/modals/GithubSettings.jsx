import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { fetchEngineeringConfig, saveEngineeringConfig, syncEngineering, fetchStatusConfig, saveStatusConfig } from '../../api'
import Modal from './Modal'
import './GithubSettings.css'

export default function GithubSettings({ onClose, onSynced }) {
  const [copiedInstall, setCopiedInstall] = useState(false)
  const [token, setToken] = useState('')
  const [repos, setRepos] = useState('')
  const [statusConfig, setStatusConfig] = useState({})
  const [configured, setConfigured] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchEngineeringConfig()
      .then((cfg) => {
        setConfigured(cfg.configured)
        setRepos(cfg.repos.join('\n'))
        return fetchStatusConfig().then(setStatusConfig).catch(() => {})
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setError('')
    setSyncResult(null)
    try {
      const repoList = repos.split('\n').map((r) => r.trim()).filter(Boolean)
      await Promise.all([
        saveEngineeringConfig({ token: token.trim(), repos: repoList }),
        saveStatusConfig(statusConfig),
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
            {`curl ${window.location.origin}/api/bridge/install.py | python3`}
          </code>
          <button
            className="gh-install-copy"
            onClick={() => {
              navigator.clipboard.writeText(`curl ${window.location.origin}/api/bridge/install.py | python3`)
              setCopiedInstall(true)
              setTimeout(() => setCopiedInstall(false), 2000)
            }}
          >
            {copiedInstall ? '✓' : 'Copy'}
          </button>
        </div>
        <p className="gh-hint gh-hint--small">
          After installing, run <code>todo-bridge --watch</code> in your project directory.
          Open any card, generate a spec, then click <strong>▶ Bridge</strong> to queue a job.
        </p>
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
