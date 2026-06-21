import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import Modal from './Modal'
import { fetchWithingsAuthUrl } from '../../api'
import './WithingsSettings.css'

function fmt(isoStr) {
  if (!isoStr) return null
  const d = new Date(isoStr)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

const GOAL_FIELDS = [
  { metric: 'steps',     label: 'Steps',        unit: 'steps / day', placeholder: '10000', step: '500',  min: '0' },
  { metric: 'weight',    label: 'Weight',        unit: 'kg',          placeholder: '75.0',  step: '0.5', min: '0' },
  { metric: 'fat_ratio', label: 'Body Fat %',    unit: '%',           placeholder: '20.0',  step: '0.1', min: '0' },
]

export default function WithingsSettings({ status, onSync, onDisconnect, onSaveGoals, syncing, healthGoals, onClose }) {
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState('')
  const [goalDraft, setGoalDraft] = useState(null)  // null = not editing; dict when editing
  const [goalSaving, setGoalSaving] = useState(false)

  const handleConnect = async () => {
    setConnecting(true)
    setError('')
    // Open a blank tab synchronously while we still have the user gesture —
    // browsers block window.open() called after an await as a popup.
    const tab = window.open('', '_blank')
    try {
      const { url } = await fetchWithingsAuthUrl()
      if (tab) {
        tab.location.href = url
      }
    } catch (e) {
      if (tab) tab.close()
      setError(e.message || 'Could not get Withings auth URL. Check that WITHINGS_CLIENT_ID and WITHINGS_SECRET are set.')
    } finally {
      setConnecting(false)
    }
  }

  const connected = status?.connected ?? false
  const lastSynced = fmt(status?.last_synced)

  const startEditGoals = () => {
    setGoalDraft({
      steps:     healthGoals?.steps     != null ? String(healthGoals.steps)     : '',
      weight:    healthGoals?.weight    != null ? String(healthGoals.weight)    : '',
      fat_ratio: healthGoals?.fat_ratio != null ? String(healthGoals.fat_ratio) : '',
    })
  }

  const handleSaveGoals = async () => {
    setGoalSaving(true)
    try {
      const payload = {}
      for (const { metric } of GOAL_FIELDS) {
        const raw = goalDraft[metric]
        payload[metric] = raw !== '' ? parseFloat(raw) : null
      }
      await onSaveGoals(payload)
      setGoalDraft(null)
    } finally {
      setGoalSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} className="withings-settings-modal">
      <Dialog.Title asChild>
        <h2>Withings</h2>
      </Dialog.Title>

      <div className="withings-settings-status">
        <span className={`withings-status-dot ${connected ? 'withings-status-dot--on' : ''}`} />
        <span>{connected ? 'Connected' : 'Not connected'}</span>
        {lastSynced && (
          <span className="withings-settings-last-sync">Last synced {lastSynced}</span>
        )}
      </div>

      <p className="withings-settings-desc">
        Connect your Withings account to automatically sync step count and body fat
        percentage. Steps can auto-complete linked habits when a goal is met.
      </p>

      <div className="withings-settings-note">
        After clicking <strong>Connect</strong>, authorise in the new tab. Once done,
        return here and click <strong>Sync now</strong> to load your data.
      </div>

      {error && <p className="withings-settings-error">{error}</p>}

      {/* Standalone health goals */}
      <div className="withings-goals-section">
        <div className="withings-goals-header">
          <span className="withings-goals-title">Health Goals</span>
          {goalDraft === null && (
            <button className="withings-goals-edit-btn" onClick={startEditGoals}>Edit</button>
          )}
        </div>
        <p className="withings-goals-desc">
          Set targets to show as reference lines on health charts — no habit required.
        </p>
        {goalDraft === null ? (
          <div className="withings-goals-list">
            {GOAL_FIELDS.map(({ metric, label, unit }) => {
              const val = healthGoals?.[metric]
              return (
                <div key={metric} className="withings-goals-row">
                  <span className="withings-goals-label">{label}</span>
                  <span className="withings-goals-value">
                    {val != null
                      ? (metric === 'steps' ? Math.round(val).toLocaleString() : val.toFixed(1)) + ' ' + unit
                      : <span className="withings-goals-unset">not set</span>}
                  </span>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="withings-goals-edit">
            {GOAL_FIELDS.map(({ metric, label, unit, placeholder, step, min }) => (
              <div key={metric} className="withings-goals-edit-row">
                <label className="withings-goals-edit-label">{label}</label>
                <div className="withings-goals-edit-input-wrap">
                  <input
                    type="number"
                    className="withings-goals-edit-input"
                    value={goalDraft[metric]}
                    onChange={(e) => setGoalDraft((d) => ({ ...d, [metric]: e.target.value }))}
                    placeholder={placeholder}
                    min={min}
                    step={step}
                  />
                  <span className="withings-goals-edit-unit">{unit}</span>
                </div>
              </div>
            ))}
            <div className="withings-goals-edit-actions">
              <button className="btn-cancel" onClick={() => setGoalDraft(null)}>Cancel</button>
              <button className="btn-save" onClick={handleSaveGoals} disabled={goalSaving}>
                {goalSaving ? 'Saving…' : 'Save goals'}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="withings-settings-actions">
        {!connected ? (
          <button
            className="btn-primary"
            onClick={handleConnect}
            disabled={connecting}
          >
            {connecting ? 'Opening…' : 'Connect Withings'}
          </button>
        ) : (
          <>
            <button
              className="btn-primary"
              onClick={onSync}
              disabled={syncing}
            >
              {syncing ? 'Syncing…' : 'Sync now'}
            </button>
            <button
              className="withings-settings-btn-disconnect"
              onClick={onDisconnect}
            >
              Disconnect
            </button>
          </>
        )}
        {connected && (
          <button className="btn-ghost" onClick={handleConnect} disabled={connecting}>
            Reconnect
          </button>
        )}
      </div>
    </Modal>
  )
}
