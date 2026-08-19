import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ChevronUpIcon, ChevronDownIcon } from '@radix-ui/react-icons'
import { fetchNavPreferences, saveNavPreferences } from '../../api'
import { NAV_ITEMS, orderedNavItems } from '../../lib/navItems'
import Modal from './Modal'
import './NavigationSettings.css'

export default function NavigationSettings({ onClose, onSaved }) {
  const [order, setOrder] = useState(NAV_ITEMS.map((item) => item.page))
  const [defaultPage, setDefaultPage] = useState('today')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchNavPreferences()
      .then((prefs) => {
        setOrder(prefs.order)
        setDefaultPage(prefs.default_page)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const moveItem = (idx, direction) => {
    const target = idx + direction
    if (target < 0 || target >= order.length) return
    setOrder((prev) => {
      const next = [...prev]
      ;[next[idx], next[target]] = [next[target], next[idx]]
      return next
    })
  }

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      await saveNavPreferences({ order, default_page: defaultPage })
      onSaved?.({ order, default_page: defaultPage })
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const navItems = orderedNavItems(order)

  return (
    <Modal onClose={onClose} className="modal--sm nav-settings-modal">
      <Dialog.Title asChild><h2>Navigation</h2></Dialog.Title>
      <p className="nav-settings-hint">
        Reorder the sidebar and mobile nav, and choose which page opens by default.
      </p>

      {loading && <p className="nav-settings-loading">Loading…</p>}

      {!loading && (
        <>
          <ul className="nav-settings-list">
            {navItems.map(({ page, label, Icon }, idx) => (
              <li key={page} className="nav-settings-row">
                <span className="nav-settings-row-icon"><Icon /></span>
                <span className="nav-settings-row-label">{label}</span>
                <div className="nav-settings-row-moves">
                  <button
                    type="button"
                    className="nav-settings-move-btn"
                    onClick={() => moveItem(idx, -1)}
                    disabled={idx === 0}
                    aria-label={`Move ${label} up`}
                  >
                    <ChevronUpIcon />
                  </button>
                  <button
                    type="button"
                    className="nav-settings-move-btn"
                    onClick={() => moveItem(idx, 1)}
                    disabled={idx === navItems.length - 1}
                    aria-label={`Move ${label} down`}
                  >
                    <ChevronDownIcon />
                  </button>
                </div>
              </li>
            ))}
          </ul>

          <div className="nav-settings-default">
            <label htmlFor="nav-default-page" className="nav-settings-default-label">
              Default page
            </label>
            <select
              id="nav-default-page"
              className="nav-settings-default-select"
              value={defaultPage}
              onChange={(e) => setDefaultPage(e.target.value)}
            >
              {NAV_ITEMS.map(({ page, label }) => (
                <option key={page} value={page}>{label}</option>
              ))}
            </select>
          </div>
        </>
      )}

      {error && <p className="form-error">{error}</p>}

      <div className="modal-footer">
        <button className="btn-cancel" onClick={onClose}>Cancel</button>
        <button className="btn-save" onClick={handleSave} disabled={saving || loading}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
