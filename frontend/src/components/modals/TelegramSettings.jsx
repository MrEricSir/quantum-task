import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import Modal from './Modal'
import { fetchTelegramConfig, saveTelegramConfig, testTelegramConfig } from '../../api'
import './TelegramSettings.css'

export default function TelegramSettings({ onClose }) {
  const [config, setConfig] = useState({
    bot_token: '',
    chat_id: '',
    schedule_hour: 7,
    tz_offset: new Date().getTimezoneOffset(),
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null) // null | { ok, error }
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetchTelegramConfig()
      .then(data => {
        const hour = data.schedule_time ? parseInt(data.schedule_time.split(':')[0], 10) : 7
        setConfig(c => ({ ...c, ...data, schedule_hour: isNaN(hour) ? 7 : hour }))
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const toApiConfig = () => ({
    bot_token: config.bot_token,
    chat_id: config.chat_id,
    schedule_time: `${String(config.schedule_hour).padStart(2, '0')}:00`,
    tz_offset: new Date().getTimezoneOffset(),  // always use current browser tz, never a stale stored value
  })

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      await saveTelegramConfig(toApiConfig())
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      await saveTelegramConfig(toApiConfig())
    } catch (e) {
      setTestResult({ ok: false, error: `Could not save config: ${e.message}` })
      setTesting(false)
      return
    }
    try {
      const result = await testTelegramConfig()
      setTestResult(result)
    } catch (e) {
      setTestResult({ ok: false, error: `Request failed: ${e.message}` })
    } finally {
      setTesting(false)
    }
  }

  const configured = config.bot_token && config.chat_id

  return (
    <Modal onClose={onClose} className="telegram-settings-modal">
      <Dialog.Title asChild>
        <h2>Telegram Briefing</h2>
      </Dialog.Title>

      <p className="telegram-settings-desc">
        Sends your daily briefing to a Telegram chat each morning. You'll need a
        Telegram bot token and your chat ID.
      </p>

      <div className="telegram-setup-steps">
        <div className="telegram-step">
          <span className="telegram-step-num">1</span>
          <span>
            Message <strong>@BotFather</strong> on Telegram, send{' '}
            <code>/newbot</code>, and copy the token it gives you.
          </span>
        </div>
        <div className="telegram-step">
          <span className="telegram-step-num">2</span>
          <span>
            Message your new bot, then visit{' '}
            <code>https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</code>{' '}
            to find your numeric chat ID in the response.
          </span>
        </div>
      </div>

      {loading ? (
        <p className="telegram-loading">Loading…</p>
      ) : (
        <div className="telegram-fields">
          <label className="telegram-label">
            Bot token
            <input
              className="telegram-input"
              type="password"
              placeholder="1234567890:ABCdef..."
              value={config.bot_token}
              onChange={e => setConfig(c => ({ ...c, bot_token: e.target.value }))}
              autoComplete="off"
            />
          </label>

          <label className="telegram-label">
            Chat ID
            <input
              className="telegram-input"
              type="text"
              placeholder="123456789"
              value={config.chat_id}
              onChange={e => setConfig(c => ({ ...c, chat_id: e.target.value }))}
            />
          </label>

          <label className="telegram-label">
            Send time (local)
            <select
              className="telegram-input telegram-input--select"
              value={config.schedule_hour}
              onChange={e => setConfig(c => ({ ...c, schedule_hour: parseInt(e.target.value, 10) }))}
            >
              {Array.from({ length: 24 }, (_, h) => {
                const label = h === 0 ? '12 AM (midnight)' : h < 12 ? `${h} AM` : h === 12 ? '12 PM (noon)' : `${h - 12} PM`
                return <option key={h} value={h}>{label}</option>
              })}
            </select>
          </label>
        </div>
      )}

      {testResult && (
        <p className={`telegram-result ${testResult.ok ? 'telegram-result--ok' : 'telegram-result--err'}`}>
          {testResult.ok ? '✓ Message sent — check your Telegram.' : `✗ ${testResult.error}`}
        </p>
      )}

      <div className="telegram-actions">
        <button
          className="btn-ghost"
          onClick={handleTest}
          disabled={testing || loading || !configured}
          title={!configured ? 'Enter bot token and chat ID first' : ''}
        >
          {testing ? 'Sending…' : 'Send test now'}
        </button>
        <button
          className="btn-primary"
          onClick={handleSave}
          disabled={saving || loading}
        >
          {saved ? 'Saved!' : saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
