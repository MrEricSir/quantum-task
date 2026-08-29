import { useEffect, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { CopyIcon, CheckIcon } from '@radix-ui/react-icons'
import Modal from './Modal'
import { fetchTagReport, fetchTagReportPeriodCounts } from '../../api'
import './TagReportModal.css'

// Mirrors backend/reports/generate.py's PERIOD_CHOICES.
const PERIOD_OPTIONS = [
  { value: 'today', label: 'Today' },
  { value: 'this_week', label: 'This week' },
  { value: 'last_week', label: 'Last week' },
  { value: 'this_month', label: 'This month' },
  { value: 'last_month', label: 'Last month' },
  { value: 'last_7_days', label: 'Last 7 days' },
  { value: 'last_30_days', label: 'Last 30 days' },
]

export default function TagReportModal({ tag, onClose }) {
  const [mode, setMode] = useState('done')
  const [period, setPeriod] = useState('this_week')
  const [customRange, setCustomRange] = useState(false)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [report, setReport] = useState(null)
  const [copied, setCopied] = useState(false)
  const [counts, setCounts] = useState(null)

  // Which quick-pick periods actually have something to report, so we can
  // disable the empty ones before the user wastes a click finding out. Fails
  // open (leaves periods enabled) if the counts call itself fails -- an
  // unrelated network hiccup here shouldn't block the report feature.
  useEffect(() => {
    let cancelled = false
    setCounts(null)
    fetchTagReportPeriodCounts(tag.id, mode)
      .then((c) => { if (!cancelled) setCounts(c) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [tag.id, mode])

  // If the selected period turns out to be empty once counts load, jump to
  // the first non-empty one instead of leaving a doomed selection active.
  useEffect(() => {
    if (customRange || !counts) return
    if (counts[period] > 0) return
    const firstNonEmpty = PERIOD_OPTIONS.find((opt) => counts[opt.value] > 0)
    if (firstNonEmpty) setPeriod(firstNonEmpty.value)
  }, [counts]) // eslint-disable-line react-hooks/exhaustive-deps

  const generateDisabled = loading || (!customRange && counts && !counts[period])

  const handleGenerate = async () => {
    if (customRange && (!start || !end)) { setError('Pick both a start and end date.'); return }
    setLoading(true)
    setError('')
    setCopied(false)
    try {
      const result = await fetchTagReport(tag.id, mode, customRange ? { start, end } : { period })
      setReport(result)
    } catch (e) {
      setError(e.message || 'Failed to generate report')
      setReport(null)
    } finally {
      setLoading(false)
    }
  }

  const handleCopy = () => {
    if (!report) return
    navigator.clipboard.writeText(report.markdown).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <Modal onClose={onClose} className="modal--sm tag-report-modal">
      <Dialog.Title asChild><h2>Report: {tag.name}</h2></Dialog.Title>

      <div className="tag-report-mode-row" role="group" aria-label="Report type">
        <button
          type="button"
          className={`tag-report-mode-btn${mode === 'done' ? ' tag-report-mode-btn--active' : ''}`}
          onClick={() => setMode('done')}
        >
          Done
        </button>
        <button
          type="button"
          className={`tag-report-mode-btn${mode === 'todo' ? ' tag-report-mode-btn--active' : ''}`}
          onClick={() => setMode('todo')}
        >
          To do
        </button>
      </div>

      {!customRange ? (
        <div className="tag-report-period-row">
          {PERIOD_OPTIONS.map((opt) => {
            const count = counts?.[opt.value]
            const empty = counts && !count
            return (
              <button
                key={opt.value}
                type="button"
                className={`tag-report-period-btn${period === opt.value ? ' tag-report-period-btn--active' : ''}${empty ? ' tag-report-period-btn--empty' : ''}`}
                onClick={() => setPeriod(opt.value)}
                disabled={empty}
                title={empty ? 'Nothing to report for this period' : undefined}
              >
                {opt.label}{counts && <span className="tag-report-period-count">{count}</span>}
              </button>
            )
          })}
          <button type="button" className="tag-report-custom-link" onClick={() => setCustomRange(true)}>
            Custom range…
          </button>
        </div>
      ) : (
        <div className="tag-report-custom-row">
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Start date" />
          <span className="tag-report-custom-sep">to</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="End date" />
          <button type="button" className="tag-report-custom-link" onClick={() => setCustomRange(false)}>
            Use a quick period instead
          </button>
        </div>
      )}

      <div className="tag-report-generate-row">
        <button className="btn-save" onClick={handleGenerate} disabled={generateDisabled}>
          {loading ? 'Generating…' : 'Generate'}
        </button>
      </div>

      {error && <p className="form-error">{error}</p>}

      {report && (
        <div className="tag-report-result">
          <div className="tag-report-result-header">
            <span className="tag-report-result-count">
              {report.count} item{report.count !== 1 ? 's' : ''}
            </span>
            <button className="tag-report-copy-btn" onClick={handleCopy} title="Copy">
              {copied ? <CheckIcon /> : <CopyIcon />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <pre className="tag-report-result-text">{report.markdown}</pre>
        </div>
      )}

      <div className="modal-footer">
        <button className="btn-cancel" onClick={onClose}>Close</button>
      </div>
    </Modal>
  )
}
