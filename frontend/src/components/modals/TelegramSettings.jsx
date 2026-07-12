import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import Modal from './Modal'
import { fetchTelegramConfig, saveTelegramConfig, testTelegramConfig, registerTelegramWebhook } from '../../api'
import './TelegramSettings.css'

export default function TelegramSettings({ onClose }) {
  const [config, setConfig] = useState({
    bot_token: '',
    chat_id: '',
    schedule_hour: 7,
    habit_reminder_hour: '',   // '' = disabled
    overdue_nudge_hour: '',    // '' = disabled
    tz_offset: new Date().getTimezoneOffset(),
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [testResult, setTestResult] = useState(null) // null | { ok, error }
  const [registerResult, setRegisterResult] = useState(null) // null | { ok, error?, webhook_url? }
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetchTelegramConfig()
      .then(data => {
        const hour = data.schedule_time ? parseInt(data.schedule_time.split(':')[0], 10) : 7
        const habitH = data.habit_reminder_time ? parseInt(data.habit_reminder_time.split(':')[0], 10) : ''
        const overdueH = data.overdue_nudge_time ? parseInt(data.overdue_nudge_time.split(':')[0], 10) : ''
        setConfig(c => ({
          ...c, ...data,
          schedule_hour: isNaN(hour) ? 7 : hour,
          habit_reminder_hour: habitH === '' ? '' : (isNaN(habitH) ? '' : habitH),
          overdue_nudge_hour:  overdueH === '' ? '' : (isNaN(overdueH) ? '' : overdueH),
        }))
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const toApiConfig = () => ({
    bot_token: config.bot_token,
    chat_id: config.chat_id,
    schedule_time: `${String(config.schedule_hour).padStart(2, '0')}:00`,
    habit_reminder_time: config.habit_reminder_hour === '' ? '' : `${String(config.habit_reminder_hour).padStart(2, '0')}:00`,
    overdue_nudge_time:  config.overdue_nudge_hour  === '' ? '' : `${String(config.overdue_nudge_hour).padStart(2, '0')}:00`,
    tz_offset: new Date().getTimezoneOffset(),
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

  const handleRegister = async () => {
    setRegistering(true)
    setRegisterResult(null)
    try {
      await saveTelegramConfig(toApiConfig())
    } catch (e) {
      setRegisterResult({ ok: false, error: `Could not save config: ${e.message}` })
      setRegistering(false)
      return
    }
    try {
      const result = await registerTelegramWebhook()
      setRegisterResult(result)
    } catch (e) {
      setRegisterResult({ ok: false, error: `Request failed: ${e.message}` })
    } finally {
      setRegistering(false)
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
        <h2>Telegram</h2>
      </Dialog.Title>

      <p className="telegram-settings-desc">
        Connect a Telegram bot to receive your daily briefing and chat with the app —
        capture tasks, check today's list, and mark things done, all from Telegram.
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
            Morning briefing time (local)
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

          <label className="telegram-label">
            Evening habit reminder
            <select
              className="telegram-input telegram-input--select"
              value={config.habit_reminder_hour}
              onChange={e => setConfig(c => ({ ...c, habit_reminder_hour: e.target.value === '' ? '' : parseInt(e.target.value, 10) }))}
            >
              <option value="">Disabled</option>
              {Array.from({ length: 24 }, (_, h) => {
                const label = h === 0 ? '12 AM (midnight)' : h < 12 ? `${h} AM` : h === 12 ? '12 PM (noon)' : `${h - 12} PM`
                return <option key={h} value={h}>{label}</option>
              })}
            </select>
          </label>

          <label className="telegram-label">
            Midday overdue task nudge
            <select
              className="telegram-input telegram-input--select"
              value={config.overdue_nudge_hour}
              onChange={e => setConfig(c => ({ ...c, overdue_nudge_hour: e.target.value === '' ? '' : parseInt(e.target.value, 10) }))}
            >
              <option value="">Disabled</option>
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
          {testing ? 'Sending…' : 'Send test briefing'}
        </button>
        <button
          className="btn-primary"
          onClick={handleSave}
          disabled={saving || loading}
        >
          {saved ? 'Saved!' : saving ? 'Saving…' : 'Save'}
        </button>
      </div>

      <div className="telegram-divider" />

      <div className="telegram-webhook-section">
        <h3 className="telegram-webhook-title">Two-way chat</h3>
        <p className="telegram-webhook-desc">
          Register a webhook so your bot can receive messages. Once enabled, you can
          send your bot <code>today</code> to see your task list, <code>done [task]</code> to
          mark something complete, or anything else to capture a new task.
        </p>
        {registerResult && (
          <p className={`telegram-result ${registerResult.ok ? 'telegram-result--ok' : 'telegram-result--err'}`}>
            {registerResult.ok
              ? '✓ Webhook registered — your bot is ready to chat.'
              : `✗ ${registerResult.error}`}
          </p>
        )}
        <button
          className="btn-ghost"
          onClick={handleRegister}
          disabled={registering || loading || !configured}
          title={!configured ? 'Enter bot token and chat ID first' : ''}
        >
          {registering ? 'Registering…' : 'Register webhook'}
        </button>
      </div>
    </Modal>
  )
}
