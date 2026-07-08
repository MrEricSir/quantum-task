import { useState, useRef } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { parseBulkCards, localDateTime, createCard } from '../../api'
import Modal from './Modal'
import CardForm, { isoToLocal } from './CardForm'
import './QuickAddModal.css'

const SR = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition)

function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="19" x2="12" y2="22" />
      <line x1="9" y1="22" x2="15" y2="22" />
    </svg>
  )
}

const TYPE_LABELS = { task: 'Task', habit: 'Habit', goal: 'Health Goal', food: 'Food Log' }
const METRIC_LABELS = { steps: 'Steps', fat_ratio: 'Body Fat %', weight: 'Weight' }
const KG_TO_LBS = 2.20462

const SECTION_OPTIONS = [
  { value: 'section:today', label: 'Today' },
  { value: 'section:week', label: 'This Week' },
  { value: 'section:month', label: 'This Month' },
  { value: 'section:later', label: 'Stash' },
]

const HISTORY_KEY = 'globalAssistHistory'
const HISTORY_MAX = 5

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]') } catch { return [] }
}

function saveToHistory(entry) {
  const prev = loadHistory()
  const next = [entry, ...prev.filter((e) => e.prompt !== entry.prompt)].slice(0, HISTORY_MAX)
  localStorage.setItem(HISTORY_KEY, JSON.stringify(next))
}

function timeAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function parseContext(value) {
  if (!value) return { section: null, tag_id: null }
  if (value.startsWith('section:')) return { section: value.slice(8), tag_id: null }
  if (value.startsWith('tag:')) return { section: null, tag_id: parseInt(value.slice(4)) }
  return { section: null, tag_id: null }
}

function formatGoalDisplay(metric, goal, isImperial) {
  if (goal == null) return '—'
  if (metric === 'steps') return `${Math.round(goal).toLocaleString()} steps / day`
  if (metric === 'fat_ratio') return `≤ ${Number(goal).toFixed(1)}%`
  if (metric === 'weight') {
    const val = isImperial ? Math.round(goal * KG_TO_LBS * 10) / 10 : goal
    return `≤ ${val.toFixed(1)} ${isImperial ? 'lbs' : 'kg'}`
  }
  return String(goal)
}

export default function QuickAddModal({
  allTags = [],
  visibleTags = [],
  onClose,
  onSaveTask,
  onSaveHabit,
  onSaveGoals,
  onSaveStepGoal,
  onSaveFood,
  isImperial = false,
  initialText = '',
  defaultTab = 'add',
}) {
  // ── Tab ──
  const [tab, setTab] = useState(defaultTab)

  // ── Input step ──
  const [text, setText] = useState(initialText)
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState('')

  // ── Confirm / bulk-edit step ──
  const [step, setStep] = useState('input') // 'input' | 'confirm' | 'bulk-confirm' | 'bulk-edit'
  const [detectedType, setDetectedType] = useState('task')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [section, setSection] = useState('later')
  const [scheduledAt, setScheduledAt] = useState('')
  const [recurrenceRule, setRecurrenceRule] = useState('')
  const [selectedTagIds, setSelectedTagIds] = useState([])
  const [clarificationQuestion, setClarificationQuestion] = useState('')
  const [withingsMetric, setWithingsMetric] = useState(null)
  const [withingsGoal, setWithingsGoal] = useState(null)
  const [saving, setSaving] = useState(false)

  // ── Voice input ──
  const [listening, setListening] = useState(false)
  const recognitionRef = useRef(null)

  // ── Bulk confirm step ──
  const [bulkItems, setBulkItems] = useState([])
  const [editingBulkIdx, setEditingBulkIdx] = useState(null)
  const [splittingIdx, setSplittingIdx] = useState(null)
  const [splitText, setSplitText] = useState('')
  const [reparseLoading, setReparseLoading] = useState(false)

  // ── Assist tab ──
  const [contextType, setContextType] = useState('')
  const [prompt, setPrompt] = useState('')
  const [output, setOutput] = useState('')
  const [assistStatus, setAssistStatus] = useState('idle') // 'idle' | 'running' | 'done' | 'error'
  const [searching, setSearching] = useState(false)
  const [copied, setCopied] = useState(false)
  const [savedAsCard, setSavedAsCard] = useState(false)
  const [history, setHistory] = useState(() => loadHistory())
  const abortRef = useRef(null)
  const outputRef = useRef(null)

  // ── Add tab handlers ──

  const toggleTag = (id) =>
    setSelectedTagIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])

  const handleMic = () => {
    if (listening) {
      recognitionRef.current?.stop()
      return
    }
    const recognition = new SR()
    recognition.lang = 'en-US'
    recognition.interimResults = true
    recognition.continuous = false
    recognitionRef.current = recognition

    let finalTranscript = ''
    recognition.onstart = () => setListening(true)
    recognition.onresult = (e) => {
      let interim = ''
      for (const result of e.results) {
        if (result.isFinal) finalTranscript += result[0].transcript
        else interim += result[0].transcript
      }
      setText(finalTranscript + interim)
    }
    recognition.onend = () => {
      setListening(false)
      if (finalTranscript.trim()) setText(finalTranscript.trim())
    }
    recognition.onerror = () => setListening(false)
    recognition.start()
  }

  const handleParse = async () => {
    if (!text.trim()) return
    setParsing(true)
    setParseError('')
    try {
      const { items } = await parseBulkCards(text.trim())
      if (items.length === 1) {
        const result = items[0]
        const tagIds = (result.suggested_tags ?? [])
          .map((name) => allTags.find((t) => t.name.toLowerCase() === name.toLowerCase())?.id)
          .filter(Boolean)
        setDetectedType(result.type ?? 'task')
        setTitle(result.title ?? '')
        setDescription(result.description ?? '')
        setSection(result.section ?? 'later')
        setScheduledAt(result.scheduled_at ? isoToLocal(result.scheduled_at) : '')
        setRecurrenceRule(result.recurrence_rule ?? '')
        setSelectedTagIds(tagIds)
        setClarificationQuestion(result.clarification_question ?? '')
        setWithingsMetric(result.withings_metric ?? null)
        setWithingsGoal(result.withings_goal ?? null)
        setStep('confirm')
      } else {
        setBulkItems(items.map((item, i) => ({ ...item, _key: i })))
        setStep('bulk-confirm')
      }
    } catch (e) {
      setParseError(e.message || 'Failed to parse. Check your connection.')
    } finally {
      setParsing(false)
    }
  }

  const buildCardPayload = () => ({
    title: title.trim(),
    description: description.trim() || null,
    section,
    scheduled_at: scheduledAt || null,
    recurrence_rule: recurrenceRule || null,
    tag_ids: selectedTagIds,
    raw_input: text,
  })

  const handleConfirm = async () => {
    setSaving(true)
    try {
      if (detectedType === 'goal' && withingsMetric === 'steps') {
        await onSaveStepGoal(withingsGoal)
      } else if (detectedType === 'goal' && withingsMetric) {
        await onSaveGoals({ [withingsMetric]: withingsGoal })
      } else if (detectedType === 'habit') {
        await onSaveHabit({ name: title, tag_ids: selectedTagIds, withings_metric: withingsMetric || null, withings_goal: withingsGoal ?? null })
      } else if (detectedType === 'food') {
        await onSaveFood({ raw_input: text, consumed_at: localDateTime() })
      } else {
        await onSaveTask(buildCardPayload())
      }
      onClose()
    } catch {
      // keep modal open on error
    } finally {
      setSaving(false)
    }
  }

  const enterBulkEdit = (idx) => {
    const item = bulkItems[idx]
    setEditingBulkIdx(idx)
    setDetectedType(item.type ?? 'task')
    setTitle(item.title ?? '')
    setDescription(item.description ?? '')
    setSection(item.section ?? 'later')
    setScheduledAt(item.scheduled_at ? isoToLocal(item.scheduled_at) : '')
    setRecurrenceRule(item.recurrence_rule ?? '')
    setSelectedTagIds(item._tag_ids ?? (item.suggested_tags ?? [])
      .map((name) => allTags.find((t) => t.name.toLowerCase() === name.toLowerCase())?.id)
      .filter(Boolean))
    setWithingsMetric(item.withings_metric ?? null)
    setWithingsGoal(item.withings_goal ?? null)
    setClarificationQuestion('')
    setStep('bulk-edit')
  }

  const saveBulkEdit = () => {
    setBulkItems((prev) => prev.map((it, i) => i === editingBulkIdx ? {
      ...it,
      type: detectedType,
      title,
      description,
      section,
      scheduled_at: scheduledAt || null,
      recurrence_rule: recurrenceRule || null,
      _tag_ids: selectedTagIds,
      withings_metric: withingsMetric || null,
      withings_goal: withingsGoal ?? null,
    } : it))
    setStep('bulk-confirm')
  }

  const handleMerge = async (idx) => {
    const visible = bulkItems.filter((i) => !i._removed)
    const a = visible[idx]
    const b = visible[idx + 1]
    const combined = [a.source_text || a.title, b.source_text || b.title].filter(Boolean).join(' ')
    setReparseLoading(true)
    try {
      const { items: reparsed } = await parseBulkCards(combined)
      const newItems = reparsed.map((item, i) => ({ ...item, _key: Date.now() + i }))
      setBulkItems((prev) => {
        const aIdx = prev.indexOf(a)
        const bIdx = prev.indexOf(b)
        const next = prev.filter((_, i) => i !== aIdx && i !== bIdx)
        next.splice(aIdx, 0, ...newItems)
        return next
      })
    } finally {
      setReparseLoading(false)
    }
  }

  const handleSplitStart = (item) => {
    setSplitText(item.source_text || item.title || '')
    setSplittingIdx(bulkItems.indexOf(item))
  }

  const handleSplitSubmit = async () => {
    if (!splitText.trim()) { setSplittingIdx(null); return }
    setReparseLoading(true)
    try {
      const { items: reparsed } = await parseBulkCards(splitText)
      const newItems = reparsed.map((item, i) => ({ ...item, _key: Date.now() + i }))
      setBulkItems((prev) => {
        const next = [...prev]
        next.splice(splittingIdx, 1, ...newItems)
        return next
      })
      setSplittingIdx(null)
    } finally {
      setReparseLoading(false)
    }
  }

  const handleBulkConfirm = async () => {
    setSaving(true)
    for (const item of bulkItems) {
      if (item._removed) continue
      const tagIds = item._tag_ids ?? (item.suggested_tags ?? [])
        .map((name) => allTags.find((t) => t.name.toLowerCase() === name.toLowerCase())?.id)
        .filter(Boolean)
      try {
        if (item.type === 'goal' && item.withings_metric === 'steps') {
          await onSaveStepGoal(item.withings_goal)
        } else if (item.type === 'goal' && item.withings_metric) {
          await onSaveGoals({ [item.withings_metric]: item.withings_goal ?? null })
        } else if (item.type === 'habit') {
          await onSaveHabit({ name: item.title, tag_ids: tagIds, withings_metric: item.withings_metric || null, withings_goal: item.withings_goal ?? null })
        } else if (item.type === 'food') {
          await onSaveFood({ raw_input: item.source_text || item.title || text, consumed_at: localDateTime() })
        } else {
          await onSaveTask({
            title: item.title,
            description: item.description || null,
            section: item.section ?? 'later',
            scheduled_at: item.scheduled_at || null,
            recurrence_rule: item.recurrence_rule || null,
            tag_ids: tagIds,
            raw_input: text,
          })
        }
      } catch {
        // continue saving remaining items even if one fails
      }
    }
    setSaving(false)
    onClose()
  }

  const confirmDisabled = saving || (
    detectedType === 'goal'
      ? (!withingsMetric || withingsGoal == null)
      : detectedType === 'food'
        ? !text.trim()
        : !title.trim()
  )

  const renderCardFields = (idPrefix) => (
    <CardForm
      idPrefix={idPrefix}
      title={title}
      setTitle={setTitle}
      description={description}
      setDescription={setDescription}
      section={section}
      setSection={setSection}
      scheduledAt={scheduledAt}
      setScheduledAt={setScheduledAt}
      recurrenceRule={recurrenceRule}
      setRecurrenceRule={setRecurrenceRule}
      allTags={allTags}
      selectedTagIds={selectedTagIds}
      onToggleTag={toggleTag}
      autoFocus={detectedType === 'task'}
    />
  )

  // ── Assist tab handlers ──

  const runAssist = async () => {
    if (!prompt.trim() || assistStatus === 'running') return
    setAssistStatus('running')
    setOutput('')
    setSearching(false)
    setSavedAsCard(false)
    setCopied(false)
    const controller = new AbortController()
    abortRef.current = controller
    const ctx = parseContext(contextType)
    try {
      const resp = await fetch('/api/assist/global', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt.trim(), ...ctx }),
        signal: controller.signal,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let text = ''
      let done = false
      while (!done) {
        const { done: d, value } = await reader.read()
        done = d
        if (value) buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { done = true; break }
          try {
            const parsed = JSON.parse(data)
            if (parsed.searching) { setSearching(true); continue }
            if (parsed.text) {
              text += parsed.text
              setOutput(text)
              if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
            }
          } catch {}
        }
      }
      if (text) {
        saveToHistory({ prompt: prompt.trim(), output: text, ts: new Date().toISOString() })
        setHistory(loadHistory())
      }
      setAssistStatus('done')
    } catch (e) {
      if (e.name !== 'AbortError') setAssistStatus('error')
      else setAssistStatus('idle')
    }
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(output).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const handleSaveAsCard = async () => {
    if (!output || savedAsCard) return
    try {
      await createCard({
        title: prompt.trim().slice(0, 80),
        description: output,
        section: 'later',
        tag_ids: [],
      })
      setSavedAsCard(true)
    } catch {}
  }

  const showTabs = tab === 'assist' || step === 'input'

  return (
    <Modal onClose={onClose} className="modal--md quick-modal">

      {/* Tab switcher — shown on the input step and on the assist tab */}
      {showTabs && (
        <div className="quick-tabs">
          <button
            className={`quick-tab${tab === 'add' ? ' quick-tab--active' : ''}`}
            onClick={() => setTab('add')}
          >Create</button>
          <button
            className={`quick-tab${tab === 'assist' ? ' quick-tab--active' : ''}`}
            onClick={() => setTab('assist')}
          >✦ Assist</button>
        </div>
      )}

      {/* ── Add tab ── */}

      {tab === 'add' && step === 'input' && (
        <>
          <Dialog.Title asChild><h2 className="quick-add-title">Quick Add</h2></Dialog.Title>
          <p className="quick-hint">
            Describe a task, habit, food log entry, or health goal in plain language. Put multiple items on separate lines to add them all at once.
          </p>
          <div className="quick-input-wrap">
            <textarea
              className="quick-textarea"
              placeholder={"dentist appointment tomorrow at 10am\nmeditate every morning\nshopping list: milk, eggs, bread"}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleParse() }}
              autoFocus={defaultTab === 'add'}
              rows={6}
            />
            {SR && (
              <button
                type="button"
                className={`quick-mic-btn${listening ? ' quick-mic-btn--listening' : ''}`}
                onClick={handleMic}
                title={listening ? 'Stop recording' : 'Dictate'}
                aria-label={listening ? 'Stop recording' : 'Dictate task'}
              >
                <MicIcon />
              </button>
            )}
          </div>
          {parseError && (
            <p className="quick-parse-error">{parseError}</p>
          )}
          <div className="modal-footer">
            <button className="btn-cancel" onClick={onClose}>Cancel</button>
            <button className="btn-save" onClick={handleParse} disabled={!text.trim() || parsing}>
              {parsing ? 'Thinking…' : 'Add'}
            </button>
          </div>
        </>
      )}

      {tab === 'add' && step === 'bulk-confirm' && (() => {
        const visible = bulkItems.filter((i) => !i._removed)
        return (
          <>
            <Dialog.Title asChild><h2>Add {visible.length} Item{visible.length !== 1 ? 's' : ''}</h2></Dialog.Title>
            <div className="quick-bulk-list">
              {visible.map((item, visIdx) => (
                <div key={item._key}>
                  {splittingIdx === bulkItems.indexOf(item) ? (
                    <div className="quick-split-editor">
                      <p className="quick-split-hint">Edit the text and add line breaks to split into multiple items.</p>
                      <textarea
                        className="quick-textarea"
                        rows={3}
                        value={splitText}
                        onChange={(e) => setSplitText(e.target.value)}
                        autoFocus
                      />
                      <div className="quick-split-actions">
                        <button className="btn-cancel" onClick={() => setSplittingIdx(null)}>Cancel</button>
                        <button className="btn-save" onClick={handleSplitSubmit} disabled={reparseLoading || !splitText.trim()}>
                          {reparseLoading ? 'Parsing…' : 'Re-parse'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      className="quick-bulk-item"
                      onClick={() => enterBulkEdit(bulkItems.indexOf(item))}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => e.key === 'Enter' && enterBulkEdit(bulkItems.indexOf(item))}
                    >
                      <span className={`quick-bulk-type-badge quick-bulk-type-badge--${item.type}`}>
                        {TYPE_LABELS[item.type] ?? 'Task'}
                      </span>
                      <div className="quick-bulk-body">
                        <span className="quick-bulk-title">{item.title}</span>
                        {item.source_text && (
                          <span className="quick-bulk-source">"{item.source_text}"</span>
                        )}
                      </div>
                      <button
                        type="button"
                        className="quick-bulk-action"
                        onClick={(e) => { e.stopPropagation(); handleSplitStart(item) }}
                        title="Split"
                        aria-label="Split item"
                      >⌥</button>
                      <button
                        type="button"
                        className="quick-bulk-remove"
                        onClick={(e) => { e.stopPropagation(); setBulkItems((prev) =>
                          prev.map((it) => it._key === item._key ? { ...it, _removed: true } : it)
                        )}}
                        aria-label="Remove"
                      >✕</button>
                    </div>
                  )}
                  {visIdx < visible.length - 1 && (
                    <button
                      className="quick-merge-btn"
                      onClick={() => handleMerge(visIdx)}
                      disabled={reparseLoading}
                      title="Merge with next item"
                    >
                      merge ↕
                    </button>
                  )}
                </div>
              ))}
            </div>
            <div className="modal-footer">
              <button className="btn-cancel" onClick={() => setStep('input')}>Back</button>
              <button
                className="btn-save"
                onClick={handleBulkConfirm}
                disabled={saving || visible.length === 0}
              >
                {saving ? 'Saving…' : 'Add All'}
              </button>
            </div>
          </>
        )
      })()}

      {tab === 'add' && step === 'bulk-edit' && (
        <>
          <Dialog.Title asChild><h2>Edit Item</h2></Dialog.Title>

          <div className="quick-type-row">
            <span className="quick-type-label">Type</span>
            <div className="quick-type-tabs">
              {['task', 'habit'].map((t) => (
                <button key={t} type="button"
                  className={`quick-type-tab${detectedType === t ? ' quick-type-tab--active' : ''}`}
                  onClick={() => setDetectedType(t)}
                >{TYPE_LABELS[t]}</button>
              ))}
            </div>
          </div>

          {detectedType === 'task' && renderCardFields('qe')}

          {detectedType === 'habit' && (
            <div className="form-group">
              <label htmlFor="qe-habit">Habit name</label>
              <input id="qe-habit" type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
            </div>
          )}

          <div className="modal-footer">
            <button className="btn-cancel" onClick={() => setStep('bulk-confirm')}>Back</button>
            <button className="btn-save" onClick={saveBulkEdit} disabled={!title.trim()}>
              Save
            </button>
          </div>
        </>
      )}

      {tab === 'add' && step === 'confirm' && (
        <>
          <Dialog.Title asChild><h2>Confirm</h2></Dialog.Title>

          {clarificationQuestion && (
            <div className="quick-clarification">
              <span className="quick-clarification-icon">💬</span>
              {clarificationQuestion}
            </div>
          )}

          {detectedType !== 'food' && (
            <div className="quick-type-row">
              <span className="quick-type-label">Detected as</span>
              <div className="quick-type-tabs">
                {(detectedType === 'goal' ? ['goal'] : ['task', 'habit']).map((t) => (
                  <button
                    key={t}
                    type="button"
                    className={`quick-type-tab${detectedType === t ? ' quick-type-tab--active' : ''}`}
                    onClick={() => t !== 'goal' && setDetectedType(t)}
                  >
                    {TYPE_LABELS[t]}
                  </button>
                ))}
              </div>
            </div>
          )}

          {detectedType === 'task' && renderCardFields('qa')}

          {detectedType === 'habit' && (
            <div className="form-group">
              <label htmlFor="qa-habit">Habit name</label>
              <input
                id="qa-habit"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                autoFocus
              />
            </div>
          )}

          {detectedType === 'goal' && (
            <div className="quick-goal-preview">
              <div className="quick-goal-metric">{METRIC_LABELS[withingsMetric] ?? withingsMetric}</div>
              <div className="quick-goal-value">{formatGoalDisplay(withingsMetric, withingsGoal, isImperial)}</div>
            </div>
          )}

          {detectedType === 'food' && (
            <div className="quick-food-preview">
              <span className="quick-food-icon">🍽</span>
              <span className="quick-food-text">{title || text}</span>
            </div>
          )}

          <div className="modal-footer">
            <button className="btn-cancel" onClick={() => setStep('input')}>Back</button>
            <button className="btn-save" onClick={handleConfirm} disabled={confirmDisabled}>
              {saving ? 'Saving…' : detectedType === 'goal' ? 'Set Goal' : detectedType === 'food' ? 'Log Food' : `Add ${TYPE_LABELS[detectedType]}`}
            </button>
          </div>
        </>
      )}

      {/* ── Assist tab ── */}

      {tab === 'assist' && (
        <>
          <Dialog.Title asChild><h2 className="quick-add-title">✦ Assist</h2></Dialog.Title>

          {/* Context selector */}
          <div className="quick-assist-context-row">
            <label className="quick-assist-context-label" htmlFor="qa-assist-context">Cards</label>
            <select
              id="qa-assist-context"
              className="quick-assist-context-select"
              value={contextType}
              onChange={(e) => setContextType(e.target.value)}
            >
              <option value="">No cards</option>
              <optgroup label="By section">
                {SECTION_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </optgroup>
              {visibleTags.length > 0 && (
                <optgroup label="By tag">
                  {visibleTags.map((t) => (
                    <option key={t.id} value={`tag:${t.id}`}>{t.name}</option>
                  ))}
                </optgroup>
              )}
            </select>
          </div>

          {/* Prompt */}
          <textarea
            className="quick-textarea quick-assist-prompt"
            placeholder="What would you like help with?"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) runAssist() }}
            rows={5}
            autoFocus
          />

          {/* Output */}
          {(output || assistStatus === 'running') && (
            <div className="quick-assist-output-section">
              <div className="quick-assist-output-header">
                <span className="quick-assist-output-label">Output</span>
                {output && (
                  <div className="quick-assist-actions">
                    <button className="quick-assist-action-btn" onClick={handleCopy}>
                      {copied ? 'Copied!' : 'Copy'}
                    </button>
                    <button className="quick-assist-action-btn" onClick={handleSaveAsCard} disabled={savedAsCard}>
                      {savedAsCard ? 'Saved' : 'Save as card'}
                    </button>
                  </div>
                )}
              </div>
              <div
                className={`quick-assist-output${assistStatus === 'running' ? ' quick-assist-output--streaming' : ''}`}
                ref={outputRef}
              >
                {output || (
                  <span className="quick-assist-placeholder">
                    {searching ? 'Searching the web…' : 'Thinking…'}
                  </span>
                )}
              </div>
            </div>
          )}

          {assistStatus === 'error' && (
            <p className="quick-parse-error">Something went wrong. Check your connection and try again.</p>
          )}

          {/* Footer */}
          <div className="modal-footer">
            <button className="btn-cancel" onClick={onClose}>Cancel</button>
            <button
              className="btn-save"
              onClick={runAssist}
              disabled={!prompt.trim() || assistStatus === 'running'}
              aria-label="Generate"
            >
              {assistStatus === 'running'
                ? <><span className="quick-spinner" style={{ display: 'inline-block', marginRight: 6 }} />Generating…</>
                : 'Generate'}
            </button>
          </div>

          {/* History dropdown */}
          {history.length > 0 && (
            <div className="quick-assist-history-row">
              <label className="quick-assist-context-label" htmlFor="qa-assist-recent">Recent</label>
              <select
                id="qa-assist-recent"
                className="quick-assist-context-select"
                value=""
                onChange={(e) => {
                  if (!e.target.value) return
                  const entry = history[parseInt(e.target.value)]
                  if (entry) {
                    setPrompt(entry.prompt)
                    setOutput(entry.output)
                    setAssistStatus('done')
                  }
                }}
              >
                <option value="">Choose a previous query…</option>
                {history.map((entry, i) => (
                  <option key={i} value={i}>
                    {entry.prompt.length > 55 ? entry.prompt.slice(0, 55) + '…' : entry.prompt} · {timeAgo(entry.ts)}
                  </option>
                ))}
              </select>
            </div>
          )}
        </>
      )}

    </Modal>
  )
}
