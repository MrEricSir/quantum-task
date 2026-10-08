import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { fetchExperimentCategoryPreferences, saveExperimentCategoryPreferences } from '../../api'
import Modal from './Modal'
import './ExperimentCategorySettings.css'

// Labels/descriptions are hardcoded client-side (the backend only knows category ids),
// same convention as lib/navItems.js's NAV_ITEMS for the navigation preferences modal.
const CATEGORIES = [
  { id: 'activity', label: 'Activity', hint: 'Steps, workouts, weight/body-fat targets' },
  { id: 'diet',     label: 'Diet',     hint: 'Reducing or eliminating a specific food' },
  { id: 'habits',   label: 'Habits',   hint: 'Everything else (screen time, meditation, sleep timing, etc.)' },
]

export default function ExperimentCategorySettings({ onClose, onSaved }) {
  const [enabled, setEnabled] = useState(new Set(CATEGORIES.map((c) => c.id)))
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchExperimentCategoryPreferences()
      .then((prefs) => setEnabled(new Set(prefs.enabled)))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const toggle = (id) => {
    setEnabled((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      const prefs = await saveExperimentCategoryPreferences({ enabled: [...enabled] })
      onSaved?.(prefs)
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} className="modal--sm exp-cat-settings-modal">
      <Dialog.Title asChild><h2>Experiment categories</h2></Dialog.Title>
      <p className="exp-cat-settings-hint">
        Which kinds of experiments the weekly health experiment can draw from. Unchecking
        a category stops new experiments from being proposed in it — a category added
        later stays on by default unless you turn it off here.
      </p>

      {loading && <p className="exp-cat-settings-loading">Loading…</p>}

      {!loading && (
        <ul className="exp-cat-settings-list">
          {CATEGORIES.map(({ id, label, hint }) => (
            <li key={id} className="exp-cat-settings-row">
              <label className="exp-cat-settings-row-label">
                <input
                  type="checkbox"
                  checked={enabled.has(id)}
                  onChange={() => toggle(id)}
                />
                <span>
                  <span className="exp-cat-settings-row-title">{label}</span>
                  <span className="exp-cat-settings-row-hint">{hint}</span>
                </span>
              </label>
            </li>
          ))}
        </ul>
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
