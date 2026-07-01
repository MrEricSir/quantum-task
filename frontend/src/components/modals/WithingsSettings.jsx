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

const KG_TO_LBS = 2.20462

// Steps is tracked as a streak habit only — not a standalone goal.
const GOAL_FIELDS = [
  { metric: 'weight',    label: 'Weight',     step: '0.5', min: '0' },
  { metric: 'fat_ratio', label: 'Body Fat %', unit: '%',   placeholder: '20.0', step: '0.1', min: '0' },
]

export default function WithingsSettings({ status, onSync, onDisconnect, onSaveGoals, syncing, syncError, healthGoals, onClose, isImperial = false }) {
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState('')
  const [goalDraft, setGoalDraft] = useState(null)  // null = not editing; dict when editing
  const [goalSaving, setGoalSaving] = useState(false)

  const handleConnect = async () => {
    setConnecting(true)
    setError('')
    try {
      const { url } = await fetchWithingsAuthUrl()
      // Navigate the current tab directly — popup/new-tab approaches fail in modern
      // browsers when Withings redirects back cross-origin. The ?withings=connected
      // handler in App.jsx works fine without window.opener.
      window.location.href = url
    } catch (e) {
      setConnecting(false)
      setError(e.message || 'Could not get Withings auth URL. Check that WITHINGS_CLIENT_ID and WITHINGS_SECRET are set.')
    }
  }

  const connected = status?.connected ?? false
  const lastSynced = fmt(status?.last_synced)

  const startEditGoals = () => {
    const weightKg = healthGoals?.weight
    setGoalDraft({
      weight:    weightKg != null
        ? String(isImperial ? Math.round(weightKg * KG_TO_LBS * 10) / 10 : weightKg)
        : '',
      fat_ratio: healthGoals?.fat_ratio != null ? String(healthGoals.fat_ratio) : '',
    })
  }

  const handleSaveGoals = async () => {
    setGoalSaving(true)
    try {
      const payload = {}
      for (const { metric } of GOAL_FIELDS) {
        const raw = goalDraft[metric]
        if (raw === '') {
          payload[metric] = null
        } else {
          const val = parseFloat(raw)
          payload[metric] = metric === 'weight' && isImperial ? Math.round(val / KG_TO_LBS * 10) / 10 : val
        }
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
        Clicking <strong>Connect</strong> will take you to Withings to authorise.
        You'll be redirected back automatically when done.
      </div>

      {error && <p className="withings-settings-error">{error}</p>}
      {syncError === 'invalid_token' && (
        <p className="withings-settings-error">
          Withings connection expired — click <strong>Reconnect</strong> to re-authorize.
        </p>
      )}
      {syncError && syncError !== 'invalid_token' && (
        <p className="withings-settings-error">Sync failed. Check your connection and try again.</p>
      )}

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
            {GOAL_FIELDS.map(({ metric, label }) => {
              const rawVal = healthGoals?.[metric]
              const displayVal = metric === 'weight' && rawVal != null
                ? (isImperial ? Math.round(rawVal * KG_TO_LBS * 10) / 10 : rawVal)
                : rawVal
              const unit = metric === 'weight' ? (isImperial ? 'lbs' : 'kg') : '%'
              return (
                <div key={metric} className="withings-goals-row">
                  <span className="withings-goals-label">{label}</span>
                  <span className="withings-goals-value">
                    {displayVal != null
                      ? displayVal.toFixed(1) + ' ' + unit
                      : <span className="withings-goals-unset">not set</span>}
                  </span>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="withings-goals-edit">
            {GOAL_FIELDS.map(({ metric, label, step, min }) => {
              const unit = metric === 'weight' ? (isImperial ? 'lbs' : 'kg') : '%'
              const placeholder = metric === 'weight' ? (isImperial ? '165.0' : '75.0') : '20.0'
              return (
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
            )})}
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
