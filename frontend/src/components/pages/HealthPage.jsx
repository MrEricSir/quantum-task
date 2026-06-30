import { useState, useEffect } from 'react'
import { fetchHealthCorrelations, fetchHealthExperiment, dismissHealthExperiment, fetchHealthExperiments } from '../../api'
import './HealthPage.css'

// ── SVG chart primitives ──────────────────────────────────────────────────────

const CHART_W = 800
const CHART_H = 180
const PAD = { top: 16, right: 16, bottom: 32, left: 48 }
const INNER_W = CHART_W - PAD.left - PAD.right
const INNER_H = CHART_H - PAD.top - PAD.bottom

function yScale(value, min, max) {
  if (max === min) return PAD.top + INNER_H / 2
  return PAD.top + INNER_H - ((value - min) / (max - min)) * INNER_H
}

function xPos(index, count) {
  if (count <= 1) return PAD.left + INNER_W / 2
  return PAD.left + (index / (count - 1)) * INNER_W
}

function AxisY({ min, max, ticks = 4 }) {
  const step = (max - min) / ticks
  return (
    <>
      {Array.from({ length: ticks + 1 }, (_, i) => {
        const val = min + step * i
        const y = yScale(val, min, max)
        return (
          <g key={i}>
            <line x1={PAD.left} x2={PAD.left + INNER_W} y1={y} y2={y} className="chart-grid-line" />
            <text x={PAD.left - 6} y={y + 4} className="chart-axis-label" textAnchor="end">
              {Number.isInteger(step) ? Math.round(val).toLocaleString() : val.toFixed(1)}
            </text>
          </g>
        )
      })}
    </>
  )
}

// ── Shared helpers ────────────────────────────────────────────────────────────

function labelIndicesFor(count) {
  const indices = new Set()
  const step = Math.max(1, Math.floor((count - 1) / 5))
  for (let i = 0; i < count; i += step) indices.add(i)
  indices.add(count - 1)
  return indices
}

function movingAvg(data, window = 7) {
  return data.map((_, i) => {
    const slice = data.slice(Math.max(0, i - window + 1), i + 1)
    return slice.reduce((s, d) => s + d.value, 0) / slice.length
  })
}

// ── SVG tooltip ───────────────────────────────────────────────────────────────

function SvgTooltip({ tooltip }) {
  if (!tooltip) return null
  const TW = 168; const TH = 22
  const cx = Math.max(PAD.left + TW / 2, Math.min(CHART_W - PAD.right - TW / 2, tooltip.x))
  return (
    <g transform={`translate(${cx}, ${tooltip.y - 10})`} style={{ pointerEvents: 'none' }}>
      <rect x={-TW / 2} y={-TH} width={TW} height={TH} rx={4} fill="rgba(10,10,25,0.92)" stroke="rgba(255,255,255,0.12)" strokeWidth={1} />
      <text x={0} y={-6} textAnchor="middle" fontSize={10} fill="rgba(255,255,255,0.88)" fontFamily="inherit">{tooltip.label}</text>
    </g>
  )
}

// ── Steps bar chart ───────────────────────────────────────────────────────────

function StepsChart({ data, goal, completionDates }) {
  const [tooltip, setTooltip] = useState(null)

  if (!data.length) return <div className="health-chart-empty">No step data yet. Sync your Withings account.</div>

  // data is already pre-sliced by the range selector in the parent
  const avgs = movingAvg(data)
  const maxSteps = Math.max(...data.map(d => d.value), goal ?? 0, 1)
  const minSteps = 0
  const barW = Math.max(4, (INNER_W / data.length) - 2)
  const completionSet = new Set(completionDates ?? [])
  const labelIndices = labelIndicesFor(data.length)

  const avgPoints = data.map((d, i) => {
    const x = PAD.left + (INNER_W / data.length) * i + (INNER_W / data.length) / 2
    return `${x},${yScale(avgs[i], minSteps, maxSteps)}`
  }).join(' ')

  return (
    <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="health-chart-svg" aria-label="Steps bar chart">
      <AxisY min={minSteps} max={maxSteps} ticks={4} />

      {goal != null && (
        <line
          x1={PAD.left} x2={PAD.left + INNER_W}
          y1={yScale(goal, minSteps, maxSteps)} y2={yScale(goal, minSteps, maxSteps)}
          className="chart-goal-line"
        />
      )}

      {data.map((d, i) => {
        const barH = Math.max(1, yScale(minSteps, minSteps, maxSteps) - yScale(d.value, minSteps, maxSteps))
        const x = PAD.left + (INNER_W / data.length) * i + (INNER_W / data.length - barW) / 2
        const y = yScale(d.value, minSteps, maxSteps)
        const met = goal != null ? d.value >= goal : false
        const barCx = x + barW / 2
        const dateLabel = new Date(d.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
        return (
          <g key={d.date}>
            <rect
              x={x} y={y} width={barW} height={barH}
              className={`chart-bar${met ? ' chart-bar--met' : ''}`}
              onMouseEnter={() => setTooltip({ x: barCx, y, label: `${dateLabel} · ${Math.round(d.value).toLocaleString()} steps` })}
              onMouseLeave={() => setTooltip(null)}
            />
            {completionSet.has(d.date) && (
              <circle cx={barCx} cy={yScale(minSteps, minSteps, maxSteps) + 10} r={3} className="chart-completion-dot" />
            )}
            {labelIndices.has(i) && (
              <text x={barCx} y={CHART_H - 4} className="chart-axis-label" textAnchor="middle">
                {dateLabel}
              </text>
            )}
          </g>
        )
      })}

      {/* 7-day moving average */}
      {data.length > 1 && (
        <polyline points={avgPoints} className="chart-moving-avg" />
      )}

      <SvgTooltip tooltip={tooltip} />
    </svg>
  )
}

// ── Generic line chart (body fat, weight) ────────────────────────────────────

function LineChart({ data, goal, completionDates, unit = '', emptyMsg, ariaLabel }) {
  const [tooltip, setTooltip] = useState(null)

  if (!data.length) return <div className="health-chart-empty">{emptyMsg}</div>

  const values = data.map(d => d.value)
  const allVals = goal != null ? [...values, goal] : values
  const minV = Math.floor(Math.min(...allVals) - 1)
  const maxV = Math.ceil(Math.max(...allVals) + 1)
  const completionSet = new Set(completionDates ?? [])

  const points = data.map((d, i) => ({
    x: xPos(i, data.length),
    y: yScale(d.value, minV, maxV),
    date: d.date,
    value: d.value,
    completed: completionSet.has(d.date),
  }))

  const polyline = points.map(p => `${p.x},${p.y}`).join(' ')
  const labelIndices = labelIndicesFor(data.length)

  return (
    <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="health-chart-svg" aria-label={ariaLabel}>
      <AxisY min={minV} max={maxV} ticks={4} />

      {goal != null && (
        <line
          x1={PAD.left} x2={PAD.left + INNER_W}
          y1={yScale(goal, minV, maxV)} y2={yScale(goal, minV, maxV)}
          className="chart-goal-line"
        />
      )}

      {points.length > 1 && (
        <polygon
          points={[
            `${points[0].x},${yScale(minV, minV, maxV)}`,
            ...points.map(p => `${p.x},${p.y}`),
            `${points[points.length - 1].x},${yScale(minV, minV, maxV)}`,
          ].join(' ')}
          className="chart-area"
        />
      )}

      <polyline points={polyline} className="chart-line" />

      {points.map((p, i) => {
        const dateLabel = new Date(p.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
        return (
          <g key={p.date}>
            {/* Larger invisible hit area for easier hover */}
            <circle
              cx={p.x} cy={p.y} r={8}
              fill="transparent"
              style={{ cursor: 'default' }}
              onMouseEnter={() => setTooltip({ x: p.x, y: p.y, label: `${dateLabel} · ${p.value.toFixed(1)}${unit}` })}
              onMouseLeave={() => setTooltip(null)}
            />
            <circle cx={p.x} cy={p.y} r={3.5} className={`chart-dot${p.completed ? ' chart-dot--completed' : ''}`} />
            {labelIndices.has(i) && (
              <text x={p.x} y={CHART_H - 4} className="chart-axis-label" textAnchor="middle">
                {dateLabel}
              </text>
            )}
          </g>
        )
      })}

      <SvgTooltip tooltip={tooltip} />
    </svg>
  )
}

// ── Blood pressure dual-line chart ───────────────────────────────────────────

function BPChart({ sysData, diaData }) {
  const [tooltip, setTooltip] = useState(null)

  if (!sysData.length && !diaData.length) {
    return <div className="health-chart-empty">No blood pressure data yet. Take a reading with your Withings device.</div>
  }

  const sysMap = Object.fromEntries(sysData.map(d => [d.date, d.value]))
  const diaMap = Object.fromEntries(diaData.map(d => [d.date, d.value]))
  const allDates = [...new Set([...sysData.map(d => d.date), ...diaData.map(d => d.date)])].sort()
  const count = allDates.length

  const allVals = [...sysData.map(d => d.value), ...diaData.map(d => d.value)]
  const minV = Math.floor(Math.min(...allVals, 60) - 5)
  const maxV = Math.ceil(Math.max(...allVals, 140) + 5)

  const makePoints = (map) => allDates
    .map((date, i) => map[date] != null ? { x: xPos(i, count), y: yScale(map[date], minV, maxV), date, value: map[date] } : null)
    .filter(Boolean)

  const sysPoints = makePoints(sysMap)
  const diaPoints = makePoints(diaMap)
  const labelIndices = labelIndicesFor(count)

  return (
    <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="health-chart-svg" aria-label="Blood pressure chart">
      <AxisY min={minV} max={maxV} ticks={4} />

      {/* AHA Stage 1 reference lines */}
      {130 > minV && 130 < maxV && (
        <line x1={PAD.left} x2={PAD.left + INNER_W} y1={yScale(130, minV, maxV)} y2={yScale(130, minV, maxV)} className="chart-bp-ref chart-bp-ref--sys" />
      )}
      {80 > minV && 80 < maxV && (
        <line x1={PAD.left} x2={PAD.left + INNER_W} y1={yScale(80, minV, maxV)} y2={yScale(80, minV, maxV)} className="chart-bp-ref chart-bp-ref--dia" />
      )}

      {sysPoints.length > 1 && <polyline points={sysPoints.map(p => `${p.x},${p.y}`).join(' ')} className="chart-line chart-line--systolic" />}
      {diaPoints.length > 1 && <polyline points={diaPoints.map(p => `${p.x},${p.y}`).join(' ')} className="chart-line chart-line--diastolic" />}

      {allDates.map((date, i) => {
        const sys = sysMap[date]
        const dia = diaMap[date]
        if (sys == null && dia == null) return null
        const x = xPos(i, count)
        const dateLabel = new Date(date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
        const tipLabel = (sys != null && dia != null)
          ? `${dateLabel} · ${Math.round(sys)}/${Math.round(dia)} mmHg`
          : sys != null ? `${dateLabel} · ${Math.round(sys)} sys` : `${dateLabel} · ${Math.round(dia)} dia`
        const hitY = sys != null ? yScale(sys, minV, maxV) : yScale(dia, minV, maxV)
        return (
          <g key={date}>
            {sys != null && <circle cx={x} cy={yScale(sys, minV, maxV)} r={3} className="chart-dot chart-dot--systolic" />}
            {dia != null && <circle cx={x} cy={yScale(dia, minV, maxV)} r={3} className="chart-dot chart-dot--diastolic" />}
            <rect
              x={x - 10} y={PAD.top} width={20} height={INNER_H}
              fill="transparent"
              onMouseEnter={() => setTooltip({ x, y: hitY, label: tipLabel })}
              onMouseLeave={() => setTooltip(null)}
            />
            {labelIndices.has(i) && (
              <text x={x} y={CHART_H - 4} className="chart-axis-label" textAnchor="middle">{dateLabel}</text>
            )}
          </g>
        )
      })}

      <SvgTooltip tooltip={tooltip} />
    </svg>
  )
}

// ── Legend ────────────────────────────────────────────────────────────────────

function ChartLegend({ items }) {
  return (
    <div className="chart-legend">
      {items.map((item, i) => (
        <div key={i} className="chart-legend-item">
          <span className="chart-legend-swatch" style={{ background: item.color }} />
          {item.label}
        </div>
      ))}
    </div>
  )
}

// ── Correlation analysis ──────────────────────────────────────────────────────

const KG_TO_LBS = 2.20462

function fmtDelta(val, unit, isImperial = false) {
  const isWeight = unit.startsWith('kg')
  let weekly = val * 7
  let displayUnit = unit.replace('/day', '/wk')
  if (isImperial && isWeight) {
    weekly = weekly * KG_TO_LBS
    displayUnit = 'lbs/wk'
  }
  const abs = Math.abs(weekly)
  const display = abs < 0.01 ? weekly.toFixed(3) : abs < 0.1 ? weekly.toFixed(2) : weekly.toFixed(1)
  return `${weekly >= 0 ? '+' : ''}${display} ${displayUnit}`
}

function SegmentCard({ segment, isImperial }) {
  const { factor, outcome, outcome_unit, threshold, high, low } = segment
  // For weight/fat loss, a more-negative delta is better
  const highIsBetter = high.mean_delta < low.mean_delta
  return (
    <div className="seg-card">
      <div className="seg-header">
        <span className="seg-factor">{factor}</span>
        <span className="seg-outcome">→ {outcome}</span>
      </div>
      <div className="seg-rows">
        <div className={`seg-row ${highIsBetter ? 'seg-row--better' : 'seg-row--worse'}`}>
          <span className="seg-label">Above {formatThreshold(factor, threshold)} avg</span>
          <span className="seg-value">{fmtDelta(high.mean_delta, outcome_unit, isImperial)}</span>
          <span className="seg-n">n={high.n}</span>
        </div>
        <div className={`seg-row ${!highIsBetter ? 'seg-row--better' : 'seg-row--worse'}`}>
          <span className="seg-label">Below {formatThreshold(factor, threshold)} avg</span>
          <span className="seg-value">{fmtDelta(low.mean_delta, outcome_unit, isImperial)}</span>
          <span className="seg-n">n={low.n}</span>
        </div>
      </div>
    </div>
  )
}

function formatThreshold(factor, threshold) {
  if (factor === 'Daily steps') return `${Math.round(threshold).toLocaleString()} steps`
  if (factor === 'Resting heart rate') return `${Math.round(threshold)} bpm`
  if (factor === 'Habit completion rate') return `${Math.round(threshold * 100)}%`
  if (factor === 'Tasks completed') return `${Math.round(threshold)} tasks`
  return threshold
}

function CorrelationBar({ r, p, factor, outcome, n }) {
  const abs = Math.abs(r)
  const positive = r >= 0
  const sig = p != null && p < 0.05
  const borderline = p != null && p >= 0.05 && p < 0.10
  return (
    <div className="corr-row">
      <div className="corr-labels">
        <span className="corr-factor">{factor}</span>
        <span className="corr-arrow">→</span>
        <span className="corr-outcome">{outcome}</span>
        {p != null && (
          <span className={`corr-p${sig ? ' corr-p--sig' : borderline ? ' corr-p--borderline' : ''}`}>
            p={p < 0.001 ? '<0.001' : p.toFixed(3)}{sig ? ' ✓' : ''}
          </span>
        )}
      </div>
      <div className="corr-bar-wrap">
        <div className="corr-bar-track">
          <div
            className={`corr-bar-fill corr-bar-fill--${positive ? 'pos' : 'neg'}`}
            style={{ width: `${abs * 100}%` }}
          />
        </div>
        <span className={`corr-r corr-r--${positive ? 'pos' : 'neg'}`}>
          {r > 0 ? '+' : ''}{r.toFixed(2)}
        </span>
        <span className="corr-n">n={n}</span>
      </div>
    </div>
  )
}

// ── Experiment card ───────────────────────────────────────────────────────────

function ExperimentCard({ onDismiss }) {
  const [exp, setExp] = useState(null)
  const [loading, setLoading] = useState(true)
  const [dismissing, setDismissing] = useState(false)

  useEffect(() => {
    fetchHealthExperiment()
      .then(setExp)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleDismiss = async () => {
    setDismissing(true)
    try {
      await dismissHealthExperiment()
      setExp(null)
      onDismiss?.()
    } catch {
      setDismissing(false)
    }
  }

  if (loading) return null
  if (!exp) return null

  // Calculate days remaining in the experiment week
  const weekMatch = exp.week?.match(/^(\d{4})-W(\d{2})$/)
  let daysLeft = null
  if (weekMatch) {
    const y = parseInt(weekMatch[1]), w = parseInt(weekMatch[2])
    const jan4 = new Date(y, 0, 4)
    const weekStartMs = jan4.getTime() - jan4.getDay() * 86400000 + (w - 1) * 7 * 86400000
    const weekEnd = new Date(weekStartMs + 7 * 86400000)
    const today = new Date(); today.setHours(0, 0, 0, 0)
    daysLeft = Math.max(0, Math.ceil((weekEnd - today) / 86400000))
  }

  return (
    <div className="experiment-card">
      <div className="experiment-header">
        <span className="experiment-badge">This week's experiment</span>
        {daysLeft !== null && (
          <span className="experiment-days">{daysLeft} day{daysLeft !== 1 ? 's' : ''} left</span>
        )}
      </div>
      <p className="experiment-text">{exp.text}</p>
      {exp.hypothesis && (
        <p className="experiment-hypothesis">
          <span className="experiment-hypothesis-label">Hypothesis: </span>
          {exp.hypothesis}
        </p>
      )}
      {exp.needs_habit && exp.habit_id && (
        <p className="experiment-habit-note">
          A tracking habit has been created for you — check it off each day you complete the experiment.
        </p>
      )}
      <div className="experiment-footer">
        <button
          className="experiment-dismiss"
          onClick={handleDismiss}
          disabled={dismissing}
        >
          {dismissing ? 'Dismissing…' : 'Dismiss & generate new'}
        </button>
      </div>
    </div>
  )
}

// ── Experiment history ────────────────────────────────────────────────────────

function ExperimentsHistory({ isImperial }) {
  const [history, setHistory] = useState(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    fetchHealthExperiments().then(setHistory).catch(() => setHistory([]))
  }, [])

  // Only show past (dismissed) experiments
  const past = (history ?? []).filter(e => e.status === 'dismissed')
  if (!past.length) return null

  return (
    <div className="exp-history">
      <button className="corr-toggle" onClick={() => setOpen(v => !v)}>
        {open ? 'Hide' : 'Show'} past experiments ({past.length})
      </button>
      {open && (
        <div className="exp-history-list">
          {past.map(exp => {
            const hasDelta = exp.weight_delta != null || exp.fat_delta != null
            const weekLabel = exp.week
            return (
              <div key={exp.id} className="exp-history-row">
                <div className="exp-history-header">
                  <span className="exp-history-week">{weekLabel}</span>
                  {exp.habit_completion_rate != null && (
                    <span className="exp-history-rate">
                      {Math.round(exp.habit_completion_rate * 100)}% completion
                    </span>
                  )}
                </div>
                <p className="exp-history-action">{exp.action ?? exp.text}</p>
                {hasDelta && (
                  <div className="exp-history-outcome">
                    {exp.weight_delta != null && (
                      <span className="exp-history-metric">
                        Weight: <strong>{fmtDelta(exp.weight_delta, 'kg/day', isImperial)}</strong>
                        {exp.weight_baseline != null && (
                          <span className="exp-history-baseline">
                            {' '}(baseline {fmtDelta(exp.weight_baseline, 'kg/day', isImperial)})
                          </span>
                        )}
                      </span>
                    )}
                    {exp.fat_delta != null && (
                      <span className="exp-history-metric">
                        Body fat: <strong>{fmtDelta(exp.fat_delta, '%/day', false)}</strong>
                        {exp.fat_baseline != null && (
                          <span className="exp-history-baseline">
                            {' '}(baseline {fmtDelta(exp.fat_baseline, '%/day', false)})
                          </span>
                        )}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Analysis section (correlations + experiment) ──────────────────────────────

function AnalysisSection({ isImperial }) {
  const [corrData, setCorrData] = useState(null)
  const [corrLoading, setCorrLoading] = useState(true)
  const [showCorr, setShowCorr] = useState(false)
  const [expKey, setExpKey] = useState(0)  // bump to reload experiment after dismiss

  useEffect(() => {
    fetchHealthCorrelations()
      .then(setCorrData)
      .catch(() => {})
      .finally(() => setCorrLoading(false))
  }, [])

  const hasCorr = !corrLoading && corrData && (corrData.correlations?.length > 0 || corrData.segments?.length > 0)

  return (
    <section className="health-section analysis-section">
      <div className="health-section-header">
        <h3 className="health-section-title">Analysis</h3>
      </div>

      {/* Experiment subsection */}
      <div className="analysis-subsection">
        <ExperimentCard key={expKey} onDismiss={() => setExpKey(k => k + 1)} />
        <ExperimentsHistory key={expKey} isImperial={isImperial} />
      </div>

      {/* Correlations subsection */}
      {hasCorr && (() => {
        const { correlations = [], segments = [], summary, weight_n, fat_n } = corrData
        const n = Math.max(weight_n, fat_n)
        return (
          <div className="analysis-subsection">
            <div className="analysis-subsection-header">
              <span className="analysis-subsection-title">Correlations</span>
              <span className="health-section-goal">{n} weekly intervals · 90 days</span>
            </div>

            {summary && <p className="corr-summary">{summary}</p>}

            {segments.length > 0 && (
              <div className="seg-grid">
                {segments.map((s, i) => <SegmentCard key={i} segment={s} isImperial={isImperial} />)}
              </div>
            )}

            {correlations.length > 0 && (
              <div className="corr-details">
                <button className="corr-toggle" onClick={() => setShowCorr(v => !v)}>
                  {showCorr ? 'Hide' : 'Show'} correlation coefficients
                </button>
                {showCorr && (
                  <div className="corr-list">
                    {correlations.map((c, i) => (
                      <CorrelationBar key={i} r={c.r} p={c.p} factor={c.factor} outcome={c.outcome} n={c.n} />
                    ))}
                    <p className="corr-note">
                      Negative r = factor correlates with improvement · correlation ≠ causation
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })()}
    </section>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function HealthPage({ habits = [], healthData, healthGoals, withingsConnected, onOpenSettings, isImperial = false }) {
  const [range, setRange] = useState(30)

  const toDisplay = (kg) => isImperial ? Math.round(kg * KG_TO_LBS * 10) / 10 : kg
  const weightUnit = isImperial ? ' lbs' : ' kg'

  const measurements = healthData?.measurements ?? []
  const habitCompletions = healthData?.habit_completions ?? {}

  // Today / yesterday for the hero section
  const _now = new Date()
  const _pad = (n) => String(n).padStart(2, '0')
  const todayStr     = `${_now.getFullYear()}-${_pad(_now.getMonth() + 1)}-${_pad(_now.getDate())}`
  const _yest        = new Date(_now); _yest.setDate(_now.getDate() - 1)
  const yesterdayStr = `${_yest.getFullYear()}-${_pad(_yest.getMonth() + 1)}-${_pad(_yest.getDate())}`

  const todayVals = {}; const yesterdayVals = {}
  for (const m of measurements) {
    if (m.date === todayStr)     todayVals[m.metric]     = m.value
    if (m.date === yesterdayStr) yesterdayVals[m.metric] = m.value
  }

  const stepsData      = measurements.filter(m => m.metric === 'steps').slice(-range)
  const fatData        = measurements.filter(m => m.metric === 'fat_ratio').slice(-range)
  const weightData     = measurements.filter(m => m.metric === 'weight').slice(-range)
  const bpSysData      = measurements.filter(m => m.metric === 'bp_systolic').slice(-range)
  const bpDiaData      = measurements.filter(m => m.metric === 'bp_diastolic').slice(-range)
  const hrData         = measurements.filter(m => m.metric === 'heart_rate').slice(-range)
  const sleepScoreData = measurements.filter(m => m.metric === 'sleep_score').slice(-range)
  const sleepMinData   = measurements.filter(m => m.metric === 'sleep_minutes').slice(-range)
  const spo2Data       = measurements.filter(m => m.metric === 'spo2').slice(-range)

  const stepsHabits  = habits.filter(h => h.withings_metric === 'steps'    && !h.archived)
  const fatHabits    = habits.filter(h => h.withings_metric === 'fat_ratio' && !h.archived)
  const weightHabits = habits.filter(h => h.withings_metric === 'weight'    && !h.archived)

  // Most recent BP / HR / sleep / SpO2 (may predate today)
  const _allSys   = measurements.filter(m => m.metric === 'bp_systolic')
  const _allDia   = measurements.filter(m => m.metric === 'bp_diastolic')
  const _allHR    = measurements.filter(m => m.metric === 'heart_rate')
  const _allSleep = measurements.filter(m => m.metric === 'sleep_score')
  const _allSpo2  = measurements.filter(m => m.metric === 'spo2')
  const recentSys   = _allSys.length   ? _allSys[_allSys.length - 1]     : null
  const recentDia   = _allDia.length   ? _allDia[_allDia.length - 1]     : null
  const recentHR    = _allHR.length    ? _allHR[_allHR.length - 1]       : null
  const recentSleep = _allSleep.length ? _allSleep[_allSleep.length - 1] : null
  const recentSpo2  = _allSpo2.length  ? _allSpo2[_allSpo2.length - 1]  : null
  const recentBP    = recentSys && recentDia ? { sys: recentSys.value, dia: recentDia.value, date: recentSys.date } : null

  const completions = (hs) => hs.flatMap(h => habitCompletions[String(h.id)] ?? [])

  // Habit goals take priority; standalone healthGoals are the fallback
  const primaryStepsGoal  = stepsHabits[0]?.withings_goal  ?? healthGoals?.steps     ?? null
  const primaryFatGoal    = fatHabits[0]?.withings_goal    ?? healthGoals?.fat_ratio  ?? null
  const primaryWeightGoal = weightHabits[0]?.withings_goal ?? healthGoals?.weight     ?? null

  const hasData   = measurements.length > 0
  const showCharts = withingsConnected || hasData

  return (
    <div className="health-page">
      <div className="health-page-header">
        <h2 className="health-page-title">Health</h2>
        <div className="health-page-controls">
          {showCharts && (
            <div className="health-range-toggle">
              {[7, 30, 90].map(r => (
                <button
                  key={r}
                  className={`health-range-btn${range === r ? ' health-range-btn--active' : ''}`}
                  onClick={() => setRange(r)}
                >{r}d</button>
              ))}
            </div>
          )}
          <button className="health-page-settings-btn" onClick={onOpenSettings}>
            {withingsConnected ? 'Withings settings' : 'Connect Withings'}
          </button>
        </div>
      </div>

      {!showCharts && (
        <div className="health-not-connected">
          <p>Connect your Withings account to start tracking steps, body fat, and weight.</p>
          <button className="btn-primary" onClick={onOpenSettings}>Connect Withings</button>
        </div>
      )}

      {showCharts && (<>

        {/* Today at a glance */}
        {(todayVals.steps != null || todayVals.weight != null || todayVals.fat_ratio != null || recentBP != null || recentHR != null || recentSleep != null || recentSpo2 != null) && (
          <div className="health-hero">
            <div className="health-hero-label">Today</div>
            <div className="health-hero-stats">
              {todayVals.steps != null && (
                <div className="health-hero-stat">
                  <div className="health-hero-value">{Math.round(todayVals.steps).toLocaleString()}</div>
                  <div className="health-hero-metric">steps</div>
                  {primaryStepsGoal != null && (() => {
                    const pct = Math.min(100, Math.round((todayVals.steps / primaryStepsGoal) * 100))
                    return (
                      <div className="health-hero-bar-wrap">
                        <div className="health-hero-bar">
                          <div className={`health-hero-bar-fill${pct >= 100 ? ' health-hero-bar-fill--met' : ''}`} style={{ width: `${pct}%` }} />
                        </div>
                        <span className="health-hero-pct">{pct}%</span>
                      </div>
                    )
                  })()}
                </div>
              )}
              {todayVals.weight != null && (
                <div className="health-hero-stat">
                  <div className="health-hero-value">{toDisplay(todayVals.weight).toFixed(1)}</div>
                  <div className="health-hero-metric">{isImperial ? 'lbs' : 'kg'}</div>
                  {yesterdayVals.weight != null && (() => {
                    const delta = toDisplay(todayVals.weight) - toDisplay(yesterdayVals.weight)
                    return <div className={`health-hero-delta${delta <= 0 ? ' health-hero-delta--good' : ''}`}>{delta <= 0 ? '↓' : '↑'} {Math.abs(delta).toFixed(1)}</div>
                  })()}
                  {primaryWeightGoal != null && <div className="health-hero-goal">goal ≤ {toDisplay(primaryWeightGoal).toFixed(1)}{weightUnit}</div>}
                </div>
              )}
              {todayVals.fat_ratio != null && (
                <div className="health-hero-stat">
                  <div className="health-hero-value">{todayVals.fat_ratio.toFixed(1)}%</div>
                  <div className="health-hero-metric">body fat</div>
                  {yesterdayVals.fat_ratio != null && (() => {
                    const delta = todayVals.fat_ratio - yesterdayVals.fat_ratio
                    return <div className={`health-hero-delta${delta <= 0 ? ' health-hero-delta--good' : ''}`}>{delta <= 0 ? '↓' : '↑'} {Math.abs(delta).toFixed(1)}%</div>
                  })()}
                  {primaryFatGoal != null && <div className="health-hero-goal">goal ≤ {primaryFatGoal.toFixed(1)}%</div>}
                </div>
              )}
              {recentBP && (() => {
                const dateNote = recentBP.date !== todayStr
                  ? ` · ${new Date(recentBP.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
                  : ''
                const stage = recentBP.sys >= 140 || recentBP.dia >= 90 ? 'Stage 2 high'
                  : recentBP.sys >= 130 || recentBP.dia >= 80 ? 'Elevated'
                  : recentBP.sys < 90 || recentBP.dia < 60 ? 'Low' : null
                return (
                  <div className="health-hero-stat">
                    <div className="health-hero-value" style={{ fontSize: '22px' }}>{Math.round(recentBP.sys)}/{Math.round(recentBP.dia)}</div>
                    <div className="health-hero-metric">mmHg{dateNote}</div>
                    {stage && <div className="health-hero-delta">{stage}</div>}
                  </div>
                )
              })()}
              {recentHR && (() => {
                const dateNote = recentHR.date !== todayStr
                  ? ` · ${new Date(recentHR.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
                  : ''
                return (
                  <div className="health-hero-stat">
                    <div className="health-hero-value">{Math.round(recentHR.value)}</div>
                    <div className="health-hero-metric">bpm{dateNote}</div>
                  </div>
                )
              })()}
              {recentSleep && (() => {
                const dateNote = recentSleep.date !== todayStr
                  ? ` · ${new Date(recentSleep.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
                  : ''
                const score = Math.round(recentSleep.value)
                const quality = score >= 85 ? 'Good' : score >= 70 ? 'Fair' : 'Poor'
                return (
                  <div className="health-hero-stat">
                    <div className="health-hero-value">{score}</div>
                    <div className="health-hero-metric">sleep score{dateNote}</div>
                    <div className="health-hero-delta">{quality}</div>
                  </div>
                )
              })()}
              {recentSpo2 && (() => {
                const dateNote = recentSpo2.date !== todayStr
                  ? ` · ${new Date(recentSpo2.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
                  : ''
                const val = recentSpo2.value.toFixed(1)
                const low = recentSpo2.value < 95
                return (
                  <div className="health-hero-stat">
                    <div className="health-hero-value">{val}%</div>
                    <div className="health-hero-metric">SpO₂{dateNote}</div>
                    {low && <div className="health-hero-delta">Below normal</div>}
                  </div>
                )
              })()}
            </div>
          </div>
        )}

        {/* Steps */}
        <section className="health-section">
          <div className="health-section-header">
            <h3 className="health-section-title">Steps</h3>
            {primaryStepsGoal != null && (
              <span className="health-section-goal">Goal: {Math.round(primaryStepsGoal).toLocaleString()} / day</span>
            )}
          </div>
          <StepsChart data={stepsData} goal={primaryStepsGoal} completionDates={completions(stepsHabits)} />
          <ChartLegend items={[
            { color: 'var(--color-today)', label: 'Steps' },
            { color: '#22c55e', label: 'Goal met' },
            { color: '#a78bfa', label: '7-day avg' },
            ...(primaryStepsGoal != null ? [{ color: '#f59e0b', label: 'Daily goal' }] : []),
            ...(stepsHabits.length > 0 ? [{ color: '#8b5cf6', label: 'Habit completed' }] : []),
          ]} />
        </section>

        {/* Body fat */}
        <section className="health-section">
          <div className="health-section-header">
            <h3 className="health-section-title">Body Fat %</h3>
            {primaryFatGoal != null && (
              <span className="health-section-goal">Goal: ≤ {primaryFatGoal.toFixed(1)}%</span>
            )}
          </div>
          <LineChart
            data={fatData} goal={primaryFatGoal} completionDates={completions(fatHabits)}
            unit="%" emptyMsg="No body fat data yet. Weigh in with your Withings scale." ariaLabel="Body fat % line chart"
          />
          <ChartLegend items={[
            { color: 'var(--color-week)', label: 'Body fat %' },
            ...(primaryFatGoal != null ? [{ color: '#f59e0b', label: 'Target' }] : []),
          ]} />
        </section>

        {/* Weight */}
        <section className="health-section">
          <div className="health-section-header">
            <h3 className="health-section-title">Weight</h3>
            {primaryWeightGoal != null && (
              <span className="health-section-goal">Goal: ≤ {toDisplay(primaryWeightGoal).toFixed(1)}{weightUnit}</span>
            )}
          </div>
          <LineChart
            data={weightData.map(d => ({ ...d, value: toDisplay(d.value) }))}
            goal={primaryWeightGoal != null ? toDisplay(primaryWeightGoal) : null}
            completionDates={completions(weightHabits)}
            unit={weightUnit} emptyMsg="No weight data yet. Weigh in with your Withings scale." ariaLabel="Weight line chart"
          />
          <ChartLegend items={[
            { color: 'var(--color-week)', label: `Weight (${isImperial ? 'lbs' : 'kg'})` },
            ...(primaryWeightGoal != null ? [{ color: '#f59e0b', label: 'Target' }] : []),
          ]} />
        </section>

        {/* Blood Pressure */}
        {(bpSysData.length > 0 || bpDiaData.length > 0) && (
          <section className="health-section">
            <div className="health-section-header">
              <h3 className="health-section-title">Blood Pressure</h3>
            </div>
            <BPChart sysData={bpSysData} diaData={bpDiaData} />
            <ChartLegend items={[
              { color: '#f97316', label: 'Systolic' },
              { color: '#06b6d4', label: 'Diastolic' },
              { color: 'rgba(249,115,22,0.4)', label: '130 / 80 threshold' },
            ]} />
          </section>
        )}

        {/* Heart Rate */}
        {hrData.length > 0 && (
          <section className="health-section">
            <div className="health-section-header">
              <h3 className="health-section-title">Heart Rate</h3>
            </div>
            <LineChart
              data={hrData} goal={null} completionDates={[]}
              unit=" bpm" emptyMsg="No heart rate data." ariaLabel="Heart rate line chart"
            />
            <ChartLegend items={[{ color: 'var(--color-week)', label: 'Heart rate (bpm)' }]} />
          </section>
        )}

        {/* Sleep */}
        {(sleepScoreData.length > 0 || sleepMinData.length > 0) && (
          <section className="health-section">
            <div className="health-section-header">
              <h3 className="health-section-title">Sleep</h3>
            </div>
            {sleepScoreData.length > 0 && (<>
              <p className="health-chart-sublabel">Sleep score</p>
              <LineChart
                data={sleepScoreData} goal={null} completionDates={[]}
                unit="" emptyMsg="" ariaLabel="Sleep score line chart"
              />
            </>)}
            {sleepMinData.length > 0 && (<>
              <p className="health-chart-sublabel" style={{ marginTop: sleepScoreData.length > 0 ? '16px' : 0 }}>Sleep duration</p>
              <LineChart
                data={sleepMinData.map(d => ({ ...d, value: Math.round(d.value / 6) / 10 }))}
                goal={null} completionDates={[]}
                unit=" h" emptyMsg="" ariaLabel="Sleep duration line chart"
              />
            </>)}
            <ChartLegend items={[
              ...(sleepScoreData.length > 0 ? [{ color: 'var(--color-week)', label: 'Sleep score (0–100)' }] : []),
              ...(sleepMinData.length > 0   ? [{ color: 'var(--color-week)', label: 'Sleep duration (hours)' }] : []),
            ]} />
          </section>
        )}

        {/* SpO2 */}
        {spo2Data.length > 0 && (
          <section className="health-section">
            <div className="health-section-header">
              <h3 className="health-section-title">Blood Oxygen (SpO₂)</h3>
            </div>
            <LineChart
              data={spo2Data} goal={null} completionDates={[]}
              unit="%" emptyMsg="No SpO2 data." ariaLabel="SpO2 line chart"
            />
            <ChartLegend items={[{ color: 'var(--color-week)', label: 'SpO2 (%)' }]} />
          </section>
        )}

        {/* Analysis */}
        <AnalysisSection isImperial={isImperial} />

      </>)}
    </div>
  )
}
