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

export default function WithingsSettings({ status, onSync, onDisconnect, syncing, onClose }) {
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState('')

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
