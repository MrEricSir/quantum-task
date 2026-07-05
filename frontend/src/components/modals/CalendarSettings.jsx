import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Cross2Icon, PlusIcon, CopyIcon, CheckIcon } from '@radix-ui/react-icons'
import {
  fetchCalendarMappings, saveCalendarMappings, fetchExportToken, rotateExportToken,
  fetchDiscoveryFeeds, saveDiscoveryFeeds, fetchDiscoveryInterests, saveDiscoveryInterests, testDiscoveryFeeds,
} from '../../api'
import Modal from './Modal'
import './CalendarSettings.css'

function emptyFeed(tags) {
  return { id: null, name: '', ical_url: '', tag_id: tags[0]?.id ?? null }
}

function emptyDiscFeed() {
  return { id: null, name: '', ical_url: '' }
}

export default function CalendarSettings({ tags, onClose, onDiscoverySaved }) {
  const [feeds, setFeeds] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [exportTagId, setExportTagId] = useState(null)
  const [copied, setCopied] = useState(false)
  const [exportToken, setExportToken] = useState('')
  const [rotating, setRotating] = useState(false)

  // Discovery state
  const [discFeeds, setDiscFeeds] = useState([])
  const [interests, setInterests] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResults, setTestResults] = useState(null)

  const exportUrl = exportToken
    ? `${window.location.origin}/api/calendar/export.ics?token=${exportToken}${exportTagId != null ? `&tag_id=${exportTagId}` : ''}`
    : ''

  const handleCopy = () => {
    navigator.clipboard.writeText(exportUrl).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  useEffect(() => {
    Promise.all([
      fetchCalendarMappings(),
      fetchExportToken(),
      fetchDiscoveryFeeds(),
      fetchDiscoveryInterests(),
    ])
      .then(([calFeeds, token, discFeedsData, interestData]) => {
        setFeeds(calFeeds)
        setExportToken(token)
        setDiscFeeds(discFeedsData)
        setInterests(interestData.interests || '')
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const addFeed = () => setFeeds((prev) => [...prev, emptyFeed(tags)])
  const updateFeed = (idx, key, value) =>
    setFeeds((prev) => prev.map((f, i) => (i === idx ? { ...f, [key]: value } : f)))
  const removeFeed = (idx) =>
    setFeeds((prev) => prev.filter((_, i) => i !== idx))

  const addDiscFeed = () => setDiscFeeds((prev) => [...prev, emptyDiscFeed()])
  const updateDiscFeed = (idx, key, value) =>
    setDiscFeeds((prev) => prev.map((f, i) => (i === idx ? { ...f, [key]: value } : f)))
  const removeDiscFeed = (idx) =>
    setDiscFeeds((prev) => prev.filter((_, i) => i !== idx))

  const handleTest = async () => {
    setTesting(true)
    setTestResults(null)
    try {
      const validFeeds = discFeeds.filter((f) => f.ical_url.trim())
      await saveDiscoveryFeeds(validFeeds.map((f) => ({ id: f.id, name: f.name.trim(), ical_url: f.ical_url.trim() })))
      setTestResults(await testDiscoveryFeeds())
    } catch (e) {
      setError(e.message)
    } finally {
      setTesting(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      const calPayload = feeds
        .filter((f) => f.ical_url?.trim() && f.tag_id != null)
        .map((f) => ({ id: f.id, tag_id: Number(f.tag_id), ical_url: f.ical_url.trim(), name: f.name.trim() }))
      const discPayload = discFeeds
        .filter((f) => f.ical_url.trim())
        .map((f) => ({ id: f.id, name: f.name.trim(), ical_url: f.ical_url.trim() }))
      await Promise.all([
        saveCalendarMappings(calPayload),
        saveDiscoveryFeeds(discPayload),
        saveDiscoveryInterests(interests),
      ])
      onDiscoverySaved?.()
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} className="modal--md cal-settings-modal">
      <Dialog.Title asChild><h2>Calendar Settings</h2></Dialog.Title>
      <p className="cal-settings-hint">
        Add one entry per calendar feed. Each feed is assigned a tag — events
        from that feed will appear with that tag's color. Multiple feeds can
        share the same tag.
      </p>
      <p className="cal-settings-hint">
        To get a feed URL: Google Calendar → Settings → [Calendar name] →
        "Secret address in iCal format". Subscribe to each calendar separately
        for best results.
      </p>

      {loading && <p className="cal-loading">Loading…</p>}

      {!loading && (
        <div className="cal-feed-list">
          {tags.length === 0 && (
            <p className="cal-empty">No tags yet. Create tags first, then add calendar feeds.</p>
          )}

          {feeds.map((feed, idx) => (
            <div key={idx} className="cal-feed-card">
              <div className="cal-feed-top-row">
                <input
                  type="text"
                  className="cal-feed-name-input"
                  placeholder="Feed name (e.g. Personal, Work)"
                  value={feed.name}
                  onChange={(e) => updateFeed(idx, 'name', e.target.value)}
                />
                <button
                  type="button"
                  className="cal-feed-remove"
                  onClick={() => removeFeed(idx)}
                  title="Remove feed"
                  aria-label="Remove feed"
                >
                  <Cross2Icon />
                </button>
              </div>
              <div className="cal-feed-tags">
                {tags.map((tag) => (
                  <button
                    key={tag.id}
                    type="button"
                    className="cal-feed-tag-pill"
                    style={
                      feed.tag_id === tag.id
                        ? { background: tag.color, borderColor: tag.color, color: '#fff' }
                        : { borderColor: tag.color, color: tag.color }
                    }
                    onClick={() => updateFeed(idx, 'tag_id', tag.id)}
                  >
                    {tag.name}
                  </button>
                ))}
              </div>
              <input
                type="url"
                className="cal-url-input"
                placeholder="https://calendar.google.com/calendar/ical/…"
                value={feed.ical_url}
                onChange={(e) => updateFeed(idx, 'ical_url', e.target.value)}
                spellCheck={false}
              />
            </div>
          ))}

          {tags.length > 0 && (
            <button type="button" className="cal-add-feed" onClick={addFeed}>
              <PlusIcon /> Add feed
            </button>
          )}
        </div>
      )}

      {error && <p className="form-error">{error}</p>}

      <div className="cal-section">
        <div className="cal-export-label">Event Discovery</div>
        <p className="cal-settings-hint">
          Add public iCal feeds from Meetup, Lu.ma, Eventbrite, or any community calendar.
          You can paste a Google Calendar share link directly — it'll be converted automatically.
        </p>
        <div className="cal-feed-list">
          {discFeeds.length === 0 && (
            <p className="cal-empty">No discovery feeds yet.</p>
          )}
          {discFeeds.map((feed, idx) => (
            <div key={idx} className="cal-feed-card">
              <div className="cal-feed-top-row">
                <input
                  type="text"
                  className="cal-feed-name-input"
                  placeholder="Feed name (e.g. Meetup SF)"
                  value={feed.name}
                  onChange={(e) => updateDiscFeed(idx, 'name', e.target.value)}
                />
                <button type="button" className="cal-feed-remove" onClick={() => removeDiscFeed(idx)} aria-label="Remove feed">
                  <Cross2Icon />
                </button>
              </div>
              <input
                type="url"
                className="cal-url-input"
                placeholder="https://…/feed.ics or Google Calendar share link"
                value={feed.ical_url}
                onChange={(e) => updateDiscFeed(idx, 'ical_url', e.target.value)}
                spellCheck={false}
              />
            </div>
          ))}
          <button type="button" className="cal-add-feed" onClick={addDiscFeed}>
            <PlusIcon /> Add discovery feed
          </button>
        </div>

        {discFeeds.some((f) => f.ical_url.trim()) && (
          <div className="cal-disc-test-row">
            <button type="button" className="cal-disc-test-btn" onClick={handleTest} disabled={testing}>
              {testing ? 'Testing…' : 'Test feeds'}
            </button>
            {testResults && (
              <div className="cal-disc-test-results">
                {testResults.map((r) => (
                  <div key={r.id} className={`cal-disc-test-result${r.ok ? ' cal-disc-test-result--ok' : ' cal-disc-test-result--err'}`}>
                    <span className="cal-disc-test-icon">{r.ok ? '✓' : '✕'}</span>
                    <span className="cal-disc-test-name">{r.name}</span>
                    <span className="cal-disc-test-detail">
                      {r.ok ? `${r.event_count} event${r.event_count !== 1 ? 's' : ''} found` : r.error}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="cal-export-label" style={{ marginTop: '1rem' }}>What kinds of events are you looking for?</div>
        <textarea
          className="cal-disc-interests"
          placeholder="e.g. Small group meetups, tech talks, hiking groups, creative workshops. Prefer events under 50 people."
          value={interests}
          onChange={(e) => setInterests(e.target.value)}
          rows={4}
        />
        <p className="cal-settings-hint" style={{ marginTop: '0.4rem', marginBottom: 0 }}>
          The more specific you are, the better the AI recommendations.
        </p>
      </div>

      <div className="cal-export-section">
        <div className="cal-export-label">Export tasks as iCal feed</div>
        <p className="cal-settings-hint" style={{ marginBottom: '8px' }}>
          Subscribe to this URL in Google Calendar, iCloud, or any calendar app
          to see your scheduled tasks alongside your events.
        </p>
        <div className="cal-export-tag-row">
          <button
            type="button"
            className={`cal-feed-tag-pill${exportTagId === null ? ' cal-feed-tag-pill--all' : ''}`}
            onClick={() => setExportTagId(null)}
          >
            All tasks
          </button>
          {tags.map((tag) => (
            <button
              key={tag.id}
              type="button"
              className="cal-feed-tag-pill"
              style={
                exportTagId === tag.id
                  ? { background: tag.color, borderColor: tag.color, color: '#fff' }
                  : { borderColor: tag.color, color: tag.color }
              }
              onClick={() => setExportTagId(tag.id)}
            >
              {tag.name}
            </button>
          ))}
        </div>
        <div className="cal-export-url-row">
          <input
            type="text"
            className="cal-url-input cal-export-url-input"
            value={exportUrl}
            readOnly
            onFocus={(e) => e.target.select()}
          />
          <button
            type="button"
            className={`cal-copy-btn${copied ? ' cal-copy-btn--copied' : ''}`}
            onClick={handleCopy}
            title="Copy URL"
            disabled={!exportUrl}
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <p className="cal-settings-hint" style={{ marginTop: '8px', marginBottom: 0 }}>
          Anyone with this URL can read your exported tasks.{' '}
          <button
            type="button"
            className="cal-rotate-link"
            onClick={async () => {
              if (!window.confirm('Rotate the export token? Any calendar apps subscribed to the current URL will stop receiving updates until you re-subscribe with the new URL.')) return
              setRotating(true)
              try { setExportToken(await rotateExportToken()) } catch { /* ignore */ }
              setRotating(false)
            }}
            disabled={rotating}
          >
            {rotating ? 'Rotating…' : 'Rotate token'}
          </button>{' '}
          to invalidate the old URL.
        </p>
      </div>

      <div className="modal-footer">
        <button className="btn-cancel" onClick={onClose}>Cancel</button>
        <button className="btn-save" onClick={handleSave} disabled={saving || loading}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
