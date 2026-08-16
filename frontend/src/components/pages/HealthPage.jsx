import { useState, useEffect, useRef } from 'react'
import { fetchHealthCorrelations, fetchHealthExperiment, dismissHealthExperiment, fetchHealthExperiments, createFoodEntry, fetchFoodEntries, updateFoodEntry, deleteFoodEntry, localDateTime, localDate, localDateOf, fetchMoodToday, logMood, fetchFoodQualityTrend, createWorkoutEntry, fetchWorkoutEntries, fetchWorkoutChart, deleteWorkoutEntry } from '../../api'
import { useModalContext } from '../../context/ModalContext'
import { isoToLocal } from '../modals/CardForm'
import HabitsPage from './HabitsPage'
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

function segmentSignal(p, n) {
  const totalN = (n?.high?.n ?? 0) + (n?.low?.n ?? 0)
  if (p != null && p < 0.05 && totalN >= 6) return { label: 'strong signal', cls: 'seg-signal--strong' }
  if (totalN < 6) return { label: 'limited data', cls: 'seg-signal--weak' }
  if (p != null && p < 0.15) return { label: 'weak signal', cls: 'seg-signal--borderline' }
  return { label: 'no clear signal', cls: 'seg-signal--weak' }
}

function formatThreshold(factor, threshold) {
  if (factor === 'Daily steps') return `${Math.round(threshold).toLocaleString()} steps`
  if (factor === 'Resting heart rate') return `${Math.round(threshold)} bpm`
  if (factor === 'Habit completion rate') return `${Math.round(threshold * 100)}%`
  if (factor === 'Tasks completed') return `${Math.round(threshold)} tasks`
  if (factor === 'Workout days' || factor === 'Cardio days' || factor === 'Strength days')
    return `${Math.round(threshold)} days`
  return threshold
}

// ── Scatter plot (inline, shown when a correlation bar is expanded) ───────────

function ScatterPlot({ scatter, factor, outcome }) {
  const [tooltip, setTooltip] = useState(null)
  if (!scatter?.length) return null

  const xs = scatter.map(d => d.x)
  const ys = scatter.map(d => d.y)
  const minX = Math.min(...xs), maxX = Math.max(...xs)
  const minY = Math.min(...ys), maxY = Math.max(...ys)
  const padX = (maxX - minX) * 0.1 || 1
  const padY = (maxY - minY) * 0.1 || 0.001
  const W = 320, H = 140, PL = 44, PR = 8, PT = 8, PB = 28
  const IW = W - PL - PR, IH = H - PT - PB

  const sx = v => PL + ((v - (minX - padX)) / ((maxX + padX) - (minX - padX))) * IW
  const sy = v => PT + IH - ((v - (minY - padY)) / ((maxY + padY) - (minY - padY))) * IH

  // Regression line
  const n = scatter.length
  const meanX = xs.reduce((a, b) => a + b, 0) / n
  const meanY = ys.reduce((a, b) => a + b, 0) / n
  const num = xs.reduce((s, x, i) => s + (x - meanX) * (ys[i] - meanY), 0)
  const den = xs.reduce((s, x) => s + (x - meanX) ** 2, 0)
  const slope = den !== 0 ? num / den : 0
  const intercept = meanY - slope * meanX
  const x1r = minX - padX, x2r = maxX + padX
  const y1r = slope * x1r + intercept, y2r = slope * x2r + intercept

  const fmtY = v => {
    const abs = Math.abs(v * 7)
    return `${v * 7 >= 0 ? '+' : ''}${abs < 0.01 ? (v * 7).toFixed(3) : (v * 7).toFixed(2)}/wk`
  }
  const fmtX = v => v > 1000 ? `${(v / 1000).toFixed(1)}k` : v % 1 === 0 ? String(v) : v.toFixed(1)

  return (
    <div className="corr-scatter-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="corr-scatter-svg">
        {/* zero line */}
        {minY < 0 && maxY > 0 && (
          <line x1={PL} x2={PL + IW} y1={sy(0)} y2={sy(0)} stroke="rgba(255,255,255,0.12)" strokeDasharray="3 3" />
        )}
        {/* regression line */}
        <line x1={sx(x1r)} y1={sy(y1r)} x2={sx(x2r)} y2={sy(y2r)} className="scatter-regression" />
        {/* dots */}
        {scatter.map((d, i) => (
          <circle
            key={i} cx={sx(d.x)} cy={sy(d.y)} r={4}
            className="scatter-dot"
            onMouseEnter={() => setTooltip({ x: sx(d.x), y: sy(d.y), label: `${d.date}: ${fmtX(d.x)} → ${fmtY(d.y)}` })}
            onMouseLeave={() => setTooltip(null)}
          />
        ))}
        {/* x axis labels */}
        {[minX, maxX].map((v, i) => (
          <text key={i} x={i === 0 ? PL : PL + IW} y={H - 4} className="chart-axis-label" textAnchor={i === 0 ? 'start' : 'end'}>{fmtX(v)}</text>
        ))}
        {/* y axis labels */}
        {[minY, maxY].map((v, i) => (
          <text key={i} x={PL - 4} y={i === 0 ? PT + IH : PT + 8} className="chart-axis-label" textAnchor="end">{fmtY(v)}</text>
        ))}
        <SvgTooltip tooltip={tooltip} />
      </svg>
      <div className="scatter-axis-labels">
        <span>{factor}</span>
        <span>{outcome}</span>
      </div>
    </div>
  )
}

function SegmentCard({ segment, isImperial }) {
  const { factor, outcome, outcome_unit, threshold, high, low, p } = segment
  const highIsBetter = high.mean_delta < low.mean_delta
  const signal = segmentSignal(p, segment)
  return (
    <div className="seg-card">
      <div className="seg-header">
        <span className="seg-factor">{factor}</span>
        <span className="seg-outcome">→ {outcome}</span>
        <span className={`seg-signal ${signal.cls}`}>{signal.label}</span>
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

function CorrelationBar({ r, p, factor, outcome, n, scatter }) {
  const [expanded, setExpanded] = useState(false)
  const abs = Math.abs(r)
  const positive = r >= 0
  const sig = p != null && p < 0.05
  const borderline = p != null && p >= 0.05 && p < 0.10
  return (
    <div className="corr-row">
      <div className="corr-labels" onClick={() => scatter?.length && setExpanded(v => !v)} style={scatter?.length ? { cursor: 'pointer' } : {}}>
        <span className="corr-factor">{factor}</span>
        <span className="corr-arrow">→</span>
        <span className="corr-outcome">{outcome}</span>
        {p != null && (
          <span className={`corr-p${sig ? ' corr-p--sig' : borderline ? ' corr-p--borderline' : ''}`}>
            p={p < 0.001 ? '<0.001' : p.toFixed(3)}{sig ? ' ✓' : ''}
          </span>
        )}
        {scatter?.length > 0 && <span className="corr-expand">{expanded ? '▲' : '▼'}</span>}
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
      {expanded && scatter?.length > 0 && (
        <ScatterPlot scatter={scatter} factor={factor} outcome={outcome} />
      )}
    </div>
  )
}

// ── ISO week helpers (client-side) ────────────────────────────────────────────

function isoWeekDates(weekStr) {
  const m = weekStr?.match(/^(\d{4})-W(\d{2})$/)
  if (!m) return []
  const y = parseInt(m[1]), w = parseInt(m[2])
  const jan4 = new Date(y, 0, 4)
  const isoDay = (jan4.getDay() + 6) % 7
  const weekStart = new Date(jan4.getTime() - isoDay * 86400000 + (w - 1) * 7 * 86400000)
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart.getTime() + i * 86400000)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  })
}

// ── Experiment card ───────────────────────────────────────────────────────────

function ExperimentCard({ onDismiss, habitCompletions }) {
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

  const weekDates = isoWeekDates(exp.week)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const daysLeft = weekDates.length
    ? Math.max(0, Math.ceil((new Date(weekDates[6] + 'T23:59:59').getTime() - today.getTime()) / 86400000))
    : null

  // Mid-week progress: which days has the experiment habit been completed?
  const completedSet = new Set(
    exp.habit_id ? (habitCompletions?.[String(exp.habit_id)] ?? []) : []
  )
  const showProgress = exp.habit_id && weekDates.length === 7

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
      {showProgress && (
        <div className="experiment-progress">
          {weekDates.map((d, i) => {
            const done = completedSet.has(d)
            const isPast = new Date(d + 'T23:59:59') < today
            const isToday = d === localDate()
            return (
              <div
                key={d}
                className={`exp-day${done ? ' exp-day--done' : isPast ? ' exp-day--missed' : isToday ? ' exp-day--today' : ' exp-day--future'}`}
                title={d}
              >
                {done ? '✓' : ['M','T','W','T','F','S','S'][i]}
              </div>
            )
          })}
        </div>
      )}
      {exp.needs_habit && exp.habit_id && !exp.health_metric && !showProgress && (
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

function foodAdhered(exp) {
  // Was the food actually cut back, not just logged as a target? Without
  // this gate, a week where the food was still eaten as usual would still
  // get a "better/worse than usual" weight/fat verdict as if the reduction
  // had actually happened. Meaningful reduction = at most half the
  // established weekly frequency during the experiment week (both are
  // counts over a comparable ~1-week span, so directly comparable).
  if (exp.food_baseline_frequency == null || exp.food_experiment_count == null) return null
  return exp.food_experiment_count <= exp.food_baseline_frequency / 2
}

function experimentVerdict(exp) {
  if (exp.food_name != null && foodAdhered(exp) === false) {
    return { label: 'Not enough adherence to judge', cls: 'verdict--neutral' }
  }
  const wDiff = exp.weight_delta != null && exp.weight_baseline != null
    ? exp.weight_delta - exp.weight_baseline : null
  const fDiff = exp.fat_delta != null && exp.fat_baseline != null
    ? exp.fat_delta - exp.fat_baseline : null
  const diffs = [wDiff, fDiff].filter(v => v != null)
  if (diffs.length) {
    const avg = diffs.reduce((a, b) => a + b, 0) / diffs.length
    if (avg < -0.002) return { label: 'Better than usual', cls: 'verdict--good' }
    if (avg > 0.002)  return { label: 'Worse than usual',  cls: 'verdict--bad'  }
    return { label: 'No clear effect', cls: 'verdict--neutral' }
  }
  // Workout-routine experiments have no weight/fat delta to fall back on --
  // "better/worse" isn't a supportable judgment for e.g. "rowed further,"
  // so this reports significance/target-met instead of a value judgment.
  if (exp.workout_baseline_avg != null && exp.workout_experiment_avg != null) {
    if (exp.workout_p != null && exp.workout_p < 0.05) {
      return { label: 'Significant change', cls: 'verdict--good' }
    }
    if (exp.workout_target_value != null && exp.workout_experiment_avg >= exp.workout_target_value) {
      return { label: 'Target met', cls: 'verdict--neutral' }
    }
    return { label: 'No clear effect', cls: 'verdict--neutral' }
  }
  return null
}

function ExperimentsHistory({ isImperial }) {
  const [history, setHistory] = useState(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    fetchHealthExperiments().then(setHistory).catch(() => setHistory([]))
  }, [])

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
            const verdict = experimentVerdict(exp)
            return (
              <div key={exp.id} className="exp-history-row">
                <div className="exp-history-header">
                  <span className="exp-history-week">{exp.week}</span>
                  {verdict && <span className={`exp-verdict ${verdict.cls}`}>{verdict.label}</span>}
                  {exp.habit_completion_rate != null && (
                    <span className="exp-history-rate">
                      {Math.round(exp.habit_completion_rate * 100)}% completion
                    </span>
                  )}
                </div>
                <p className="exp-history-action">{exp.action ?? exp.text}</p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Routine (workout/food) experiment outcome ─────────────────────────────────

function capitalize(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s
}

function fmtWorkoutValue(v, unit) {
  if (v == null) return '—'
  const display = Number.isInteger(v) ? String(v) : v.toFixed(2)
  return unit ? `${display} ${unit}` : display
}

function fmtFrequency(v) {
  if (v == null) return '—'
  return `${Number.isInteger(v) ? v : v.toFixed(1)}x/week`
}

function ExperimentOutcomeCard({ exp }) {
  if (exp.food_name != null) {
    // Adherence is a plain count, not a significance test -- no segmentSignal
    // badge here (unlike the workout case below, which has a real p-value).
    // It still gets its own badge though: without it, a week where the food
    // was eaten as usual would look identical to a week it was actually cut.
    const adhered = foodAdhered(exp)
    return (
      <div className="seg-card">
        <div className="seg-header">
          <span className="seg-factor">{capitalize(exp.food_name)}</span>
          {exp.food_target_frequency != null && (
            <span className="seg-outcome">→ target {fmtFrequency(exp.food_target_frequency)}</span>
          )}
          {adhered != null && (
            <span className={`seg-signal ${adhered ? 'seg-signal--strong' : 'seg-signal--weak'}`}>
              {adhered ? 'adhered' : 'not adhered'}
            </span>
          )}
        </div>
        <div className="seg-rows">
          <div className="seg-row">
            <span className="seg-label">Before</span>
            <span className="seg-value">{fmtFrequency(exp.food_baseline_frequency)}</span>
            {exp.food_baseline_weeks_n != null && (
              <span className="seg-n">n={exp.food_baseline_weeks_n} weeks</span>
            )}
          </div>
          <div className="seg-row">
            <span className="seg-label">During experiment</span>
            <span className="seg-value">{exp.food_experiment_count ?? 0}x</span>
          </div>
        </div>
      </div>
    )
  }

  const signal = segmentSignal(exp.workout_p, {
    high: { n: exp.workout_experiment_n ?? 0 },
    low: { n: exp.workout_baseline_n ?? 0 },
  })
  return (
    <div className="seg-card">
      <div className="seg-header">
        <span className="seg-factor">{capitalize(exp.workout_type)}</span>
        {exp.workout_target_value != null && (
          <span className="seg-outcome">→ target {fmtWorkoutValue(exp.workout_target_value, exp.workout_unit)}</span>
        )}
        <span className={`seg-signal ${signal.cls}`}>{signal.label}</span>
      </div>
      <div className="seg-rows">
        <div className="seg-row">
          <span className="seg-label">Baseline</span>
          <span className="seg-value">{fmtWorkoutValue(exp.workout_baseline_avg, exp.workout_unit)}</span>
          <span className="seg-n">n={exp.workout_baseline_n ?? 0}</span>
        </div>
        <div className="seg-row">
          <span className="seg-label">During experiment</span>
          <span className="seg-value">{fmtWorkoutValue(exp.workout_experiment_avg, exp.workout_unit)}</span>
          <span className="seg-n">n={exp.workout_experiment_n ?? 0}</span>
        </div>
      </div>
    </div>
  )
}

function RoutineOutcomeCards({ expKey }) {
  const [history, setHistory] = useState(null)

  useEffect(() => {
    fetchHealthExperiments().then(setHistory).catch(() => setHistory([]))
  }, [expKey])

  const outcomes = (history ?? [])
    .filter(e => e.status === 'dismissed' && (
      (e.workout_type != null && e.workout_baseline_avg != null)
      || (e.food_name != null && e.food_baseline_frequency != null)
    ))
    .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
    .slice(0, 3)

  if (!outcomes.length) return null

  return (
    <div className="analysis-subsection">
      <div className="analysis-subsection-header">
        <span className="analysis-subsection-title">Routine Experiments</span>
      </div>
      <div className="seg-grid">
        {outcomes.map(exp => <ExperimentOutcomeCard key={exp.id} exp={exp} />)}
      </div>
    </div>
  )
}

// ── Analysis section (correlations + experiment) ──────────────────────────────

function AnalysisSection({ isImperial, habitCompletions }) {
  const [corrData, setCorrData] = useState(null)
  const [corrLoading, setCorrLoading] = useState(true)
  const [showCorr, setShowCorr] = useState(false)
  const [expKey, setExpKey] = useState(0)

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

      <div className="analysis-subsection">
        <ExperimentCard key={expKey} onDismiss={() => setExpKey(k => k + 1)} habitCompletions={habitCompletions} />
        <ExperimentsHistory key={expKey} isImperial={isImperial} />
      </div>

      <RoutineOutcomeCards expKey={expKey} />

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
                      <CorrelationBar key={i} r={c.r} p={c.p} factor={c.factor} outcome={c.outcome} n={c.n} scatter={c.scatter} />
                    ))}
                    <p className="corr-note">
                      Negative r = factor correlates with improvement · correlation ≠ causation · click a row to see scatter
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

// ── Workout log ───────────────────────────────────────────────────────────────

const WORKOUT_COLORS = {
  run:      'var(--color-today)',
  cycle:    '#06b6d4',
  row:      '#8b5cf6',
  swim:     '#22d3ee',
  strength: '#f97316',
  yoga:     '#a78bfa',
  sport:    '#22c55e',
  other:    '#6b7280',
}

function WorkoutLog({ range, revision = 0 }) {
  const [entries, setEntries] = useState([])
  const [chartData, setChartData] = useState([])
  const [input, setInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [date, setDate] = useState(() => localDate())
  const inputRef = useRef(null)

  const load = (d) => fetchWorkoutEntries(d).then(setEntries).catch(() => {})

  // Load chart data: single request for the full date range
  const loadChart = () => {
    const today = new Date()
    const end = localDateOf(today)
    const startD = new Date(today)
    startD.setDate(today.getDate() - (range - 1))
    const start = localDateOf(startD)
    fetchWorkoutChart(start, end).then(setChartData).catch(() => {})
  }

  useEffect(() => { load(date) }, [date, revision])   // revision triggers reload after capture modal saves
  useEffect(() => { loadChart() }, [range, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleAdd = async () => {
    if (!input.trim() || saving) return
    setSaving(true)
    try {
      await createWorkoutEntry({ raw_input: input.trim(), logged_at: localDateTime() })
      setInput('')
      await load(date)
      loadChart()
    } catch {
      // keep input
    } finally {
      setSaving(false)
      inputRef.current?.focus()
    }
  }

  const handleDelete = async (id) => {
    await deleteWorkoutEntry(id).catch(() => {})
    setEntries(prev => prev.filter(e => e.id !== id))
    loadChart()
  }

  // Chart: stacked presence dots — one column per day, colored dots per type
  const labelIndices = labelIndicesFor(chartData.length)

  return (
    <section className="health-section">
      <div className="health-section-header">
        <h3 className="health-section-title">Workouts</h3>
        <input
          type="date"
          className="food-date-picker"
          value={date}
          onChange={e => setDate(e.target.value)}
        />
      </div>

      {/* Presence chart */}
      {chartData.some(d => d.types.length > 0) && (
        <div className="workout-chart">
          {chartData.map((day, i) => (
            <div key={day.date} className="workout-chart-col">
              <div className="workout-chart-dots">
                {day.types.length > 0
                  ? day.types.map(t => (
                    <span
                      key={t}
                      className="workout-chart-dot"
                      style={{ background: WORKOUT_COLORS[t] ?? WORKOUT_COLORS.other }}
                      title={`${day.date}: ${t}`}
                    />
                  ))
                  : <span className="workout-chart-dot workout-chart-dot--empty" />
                }
              </div>
              {labelIndices.has(i) && (
                <span className="workout-chart-label">
                  {new Date(day.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Legend */}
      {chartData.some(d => d.types.length > 0) && (() => {
        const seen = [...new Set(chartData.flatMap(d => d.types))]
        return (
          <ChartLegend items={seen.map(t => ({ color: WORKOUT_COLORS[t] ?? WORKOUT_COLORS.other, label: t }))} />
        )
      })()}

      <div className="food-input-row">
        <input
          ref={inputRef}
          type="text"
          className="workout-input"
          placeholder="rowed 5000m · bench pressed 185 lbs · ran 3 miles · yoga 45 min…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleAdd()}
          disabled={saving}
        />
        <button className="food-add-btn" onClick={handleAdd} disabled={!input.trim() || saving}>
          {saving ? '…' : 'Log'}
        </button>
      </div>

      {entries.length === 0 && (
        <p className="food-empty">No workouts logged for this day.</p>
      )}

      {entries.map(e => (
        <div key={e.id} className="food-entry">
          <span className="workout-type-dot" style={{ background: WORKOUT_COLORS[e.type] ?? WORKOUT_COLORS.other }} />
          <span className="food-entry-name">{e.type}{e.value != null ? ` · ${e.value}${e.unit ? ' ' + e.unit : ''}` : ''}</span>
          {e.notes && <span className="food-entry-notes">{e.notes}</span>}
          <button className="food-entry-delete" onClick={() => handleDelete(e.id)} aria-label="Remove">✕</button>
        </div>
      ))}
    </section>
  )
}

// ── Food quality trend chart ──────────────────────────────────────────────────

function FoodQualityTrend({ range }) {
  const [data, setData] = useState([])

  useEffect(() => {
    fetchFoodQualityTrend(range).then(setData).catch(() => {})
  }, [range])

  if (data.length < 3) return null

  return (
    <div style={{ marginTop: '12px' }}>
      <p className="health-chart-sublabel">Diet quality trend (avg score/day)</p>
      <LineChart
        data={data}
        goal={null}
        completionDates={[]}
        unit="/10"
        emptyMsg=""
        ariaLabel="Food quality trend"
      />
    </div>
  )
}

// ── Food log ──────────────────────────────────────────────────────────────────

function qualityColor(q) {
  if (q == null) return 'var(--text-muted)'
  if (q >= 7) return '#22c55e'
  if (q >= 4) return '#f59e0b'
  return '#ef4444'
}

function FoodLog({ range = 30, revision = 0 }) {
  const [entries, setEntries] = useState([])
  const [input, setInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [date, setDate] = useState(() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
  })
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState(null)
  const inputRef = useRef(null)
  const editNameRef = useRef(null)

  const load = (d) => fetchFoodEntries(d).then(setEntries).catch(() => {})

  useEffect(() => { load(date) }, [date, revision]) // revision triggers reload after capture modal saves
  useEffect(() => { if (editingId !== null) editNameRef.current?.focus() }, [editingId])

  const handleAdd = async () => {
    if (!input.trim() || saving) return
    setSaving(true)
    try {
      await createFoodEntry({ raw_input: input.trim(), consumed_at: localDateTime() })
      setInput('')
      await load(date)
    } catch {
      // keep input
    } finally {
      setSaving(false)
      inputRef.current?.focus()
    }
  }

  const handleDelete = async (id) => {
    await deleteFoodEntry(id).catch(() => {})
    setEntries(prev => prev.filter(e => e.id !== id))
  }

  const startEdit = (entry) => {
    setEditingId(entry.id)
    setEditForm({
      name: entry.name,
      calories: entry.calories != null ? String(entry.calories) : '',
      quality: entry.quality != null ? String(entry.quality) : '',
      notes: entry.notes || '',
      consumed_at: isoToLocal(entry.consumed_at),
    })
  }

  const confirmEdit = async () => {
    const name = editForm.name.trim()
    if (!name) { setEditingId(null); return }
    try {
      const updated = await updateFoodEntry(editingId, {
        name,
        calories: editForm.calories !== '' ? parseInt(editForm.calories, 10) : null,
        quality: editForm.quality !== '' ? parseInt(editForm.quality, 10) : null,
        notes: editForm.notes.trim() || null,
        consumed_at: editForm.consumed_at || null,
      })
      setEntries(prev => prev.map(e => (e.id === editingId ? updated : e)).sort(
        (a, b) => new Date(a.consumed_at) - new Date(b.consumed_at)
      ))
    } catch {
      // keep editing open on failure
      return
    }
    setEditingId(null)
  }

  return (
    <section className="health-section">
      <div className="health-section-header">
        <h3 className="health-section-title">Food Log</h3>
        <input
          type="date"
          className="food-date-picker"
          value={date}
          onChange={e => setDate(e.target.value)}
        />
      </div>

      <div className="food-input-row">
        <input
          ref={inputRef}
          type="text"
          className="food-input"
          placeholder="I ate a donut · just had coffee · chicken salad for lunch…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleAdd()}
          disabled={saving}
        />
        <button
          className="food-add-btn"
          onClick={handleAdd}
          disabled={!input.trim() || saving}
        >
          {saving ? '…' : 'Log'}
        </button>
      </div>

      {entries.length === 0 && (
        <p className="food-empty">Nothing logged yet for this day.</p>
      )}

      {entries.map(entry => {
        if (editingId === entry.id) {
          return (
            <div key={entry.id} className="food-entry-edit-form">
              <input
                ref={editNameRef}
                className="food-entry-edit-input"
                value={editForm.name}
                onChange={e => setEditForm({ ...editForm, name: e.target.value })}
                onKeyDown={e => { if (e.key === 'Escape') setEditingId(null) }}
                placeholder="Name"
              />
              <input
                type="datetime-local"
                className="food-entry-edit-time"
                value={editForm.consumed_at}
                onChange={e => setEditForm({ ...editForm, consumed_at: e.target.value })}
              />
              <input
                type="number"
                className="food-entry-edit-calories"
                value={editForm.calories}
                onChange={e => setEditForm({ ...editForm, calories: e.target.value })}
                placeholder="kcal"
                min="0"
              />
              <input
                type="number"
                className="food-entry-edit-quality"
                value={editForm.quality}
                onChange={e => setEditForm({ ...editForm, quality: e.target.value })}
                placeholder="1-10"
                min="1"
                max="10"
              />
              <input
                className="food-entry-edit-notes"
                value={editForm.notes}
                onChange={e => setEditForm({ ...editForm, notes: e.target.value })}
                onKeyDown={e => { if (e.key === 'Escape') setEditingId(null) }}
                placeholder="Notes"
              />
              <div className="food-entry-edit-actions">
                <button className="food-entry-edit-save" onClick={confirmEdit}>Save</button>
                <button className="food-entry-edit-cancel" onClick={() => setEditingId(null)}>Cancel</button>
              </div>
            </div>
          )
        }

        const time = new Date(entry.consumed_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
        return (
          <div key={entry.id} className="food-entry">
            <span className="food-entry-time">{time}</span>
            <span className="food-entry-name">{entry.name}</span>
            {entry.calories != null && (
              <span className="food-entry-calories" title="Estimated calories">
                {entry.calories} kcal
              </span>
            )}
            {entry.quality != null && (
              <span
                className="food-entry-quality"
                style={{ color: qualityColor(entry.quality) }}
                title={`Quality score: ${entry.quality}/10`}
              >
                {entry.quality}/10
              </span>
            )}
            {entry.notes && (
              <span className="food-entry-notes">{entry.notes}</span>
            )}
            <button
              className="food-entry-edit"
              onClick={() => startEdit(entry)}
              aria-label="Edit"
            >✎</button>
            <button
              className="food-entry-delete"
              onClick={() => handleDelete(entry.id)}
              aria-label="Remove"
            >✕</button>
          </div>
        )
      })}
      <FoodQualityTrend range={range} />
    </section>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function HealthPage({ habits = [], archivedHabits = [], allTags = [], selectedTagIds, onToggleHabit, onAddHabit, onUpdateHabit, onDeleteHabit, onArchiveHabit, onUnarchiveHabit, healthData, healthGoals, withingsConnected, isImperial = false, healthLogRevision = 0 }) {
  const { openWithingsSettings } = useModalContext()
  const [range, setRange] = useState(30)
  const [moodToday, setMoodToday] = useState(null)

  useEffect(() => {
    fetchMoodToday().then(data => { if (data) setMoodToday(data) }).catch(() => {})
  }, [])

  const handleLogMood = async (energy, note) => {
    const result = await logMood(energy, note)
    setMoodToday(result)
  }

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
  const sleepScoreData    = measurements.filter(m => m.metric === 'sleep_score').slice(-range)
  const sleepMinData      = measurements.filter(m => m.metric === 'sleep_minutes').slice(-range)
  const sleepDeepMinData  = measurements.filter(m => m.metric === 'sleep_deep_minutes').slice(-range)
  const spo2Data          = measurements.filter(m => m.metric === 'spo2').slice(-range)

  const stepsHabits  = habits.filter(h => h.health_metric === 'steps'    && !h.archived)
  const fatHabits    = habits.filter(h => h.health_metric === 'fat_ratio' && !h.archived)
  const weightHabits = habits.filter(h => h.health_metric === 'weight'    && !h.archived)

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
  const primaryStepsGoal  = stepsHabits[0]?.health_goal  ?? healthGoals?.steps     ?? null
  const primaryFatGoal    = fatHabits[0]?.health_goal    ?? healthGoals?.fat_ratio  ?? null
  const primaryWeightGoal = weightHabits[0]?.health_goal ?? healthGoals?.weight     ?? null

  const hasData   = measurements.length > 0
  const showCharts = withingsConnected || hasData

  return (
    <div className="health-page">

      <HabitsPage
        habits={habits}
        archivedHabits={archivedHabits}
        allTags={allTags}
        selectedTagIds={selectedTagIds}
        onToggle={onToggleHabit}
        onAdd={onAddHabit}
        onUpdate={onUpdateHabit}
        onDelete={onDeleteHabit}
        onArchive={onArchiveHabit}
        onUnarchive={onUnarchiveHabit}
        isImperial={isImperial}
        moodToday={moodToday}
        onLogMood={handleLogMood}
      />

      <div className="health-page-header">
        <h3 className="health-section-title">Health</h3>
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
          <button className="health-page-settings-btn" onClick={openWithingsSettings}>
            {withingsConnected ? 'Withings settings' : 'Connect Withings'}
          </button>
        </div>
      </div>

      {/* Food and workout logs — always visible, independent of Withings */}
      <FoodLog range={range} revision={healthLogRevision} />
      <WorkoutLog range={range} revision={healthLogRevision} />

      {!showCharts && (
        <div className="health-not-connected">
          <p>Connect your Withings account to start tracking steps, body fat, and weight.</p>
          <button className="btn-primary" onClick={openWithingsSettings}>Connect Withings</button>
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
        {(sleepScoreData.length > 0 || sleepMinData.length > 0 || sleepDeepMinData.length > 0) && (
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
              <p className="health-chart-sublabel" style={{ marginTop: sleepScoreData.length > 0 ? '16px' : 0 }}>Total sleep</p>
              <LineChart
                data={sleepMinData.map(d => ({ ...d, value: d.value / 60 }))}
                goal={null} completionDates={[]}
                unit=" h" emptyMsg="" ariaLabel="Sleep duration line chart"
              />
            </>)}
            {sleepDeepMinData.length > 0 && (<>
              <p className="health-chart-sublabel" style={{ marginTop: '16px' }}>Deep sleep</p>
              <LineChart
                data={sleepDeepMinData.map(d => ({ ...d, value: d.value / 60 }))}
                goal={null} completionDates={[]}
                unit=" h" emptyMsg="" ariaLabel="Deep sleep duration line chart"
              />
            </>)}
            <ChartLegend items={[
              ...(sleepScoreData.length > 0    ? [{ color: 'var(--color-week)', label: 'Sleep score (0–100)' }] : []),
              ...(sleepMinData.length > 0      ? [{ color: 'var(--color-week)', label: 'Total sleep (hours)' }] : []),
              ...(sleepDeepMinData.length > 0  ? [{ color: 'var(--color-week)', label: 'Deep sleep (hours)' }] : []),
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
        <AnalysisSection isImperial={isImperial} habitCompletions={habitCompletions} />

      </>)}
    </div>
  )
}
