import { useState } from 'react'
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

// ── Steps bar chart ───────────────────────────────────────────────────────────

function StepsChart({ data, goal, completionDates }) {
  if (!data.length) return <div className="health-chart-empty">No step data yet. Sync your Withings account.</div>

  const last30 = data.slice(-30)
  const avgs = movingAvg(last30)
  const maxSteps = Math.max(...last30.map(d => d.value), goal ?? 0, 1)
  const minSteps = 0
  const barW = Math.max(4, (INNER_W / last30.length) - 2)
  const completionSet = new Set(completionDates ?? [])
  const labelIndices = labelIndicesFor(last30.length)

  const avgPoints = last30.map((d, i) => {
    const x = PAD.left + (INNER_W / last30.length) * i + (INNER_W / last30.length) / 2
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

      {last30.map((d, i) => {
        const barH = Math.max(1, yScale(minSteps, minSteps, maxSteps) - yScale(d.value, minSteps, maxSteps))
        const x = PAD.left + (INNER_W / last30.length) * i + (INNER_W / last30.length - barW) / 2
        const y = yScale(d.value, minSteps, maxSteps)
        const met = goal != null ? d.value >= goal : false
        return (
          <g key={d.date}>
            <rect x={x} y={y} width={barW} height={barH} className={`chart-bar${met ? ' chart-bar--met' : ''}`}>
              <title>{`${d.date}: ${Math.round(d.value).toLocaleString()} steps`}</title>
            </rect>
            {completionSet.has(d.date) && (
              <circle cx={x + barW / 2} cy={yScale(minSteps, minSteps, maxSteps) + 10} r={3} className="chart-completion-dot" />
            )}
            {labelIndices.has(i) && (
              <text x={x + barW / 2} y={CHART_H - 4} className="chart-axis-label" textAnchor="middle">
                {new Date(d.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
              </text>
            )}
          </g>
        )
      })}

      {/* 7-day moving average */}
      {last30.length > 1 && (
        <polyline points={avgPoints} className="chart-moving-avg" />
      )}
    </svg>
  )
}

// ── Generic line chart (body fat, weight) ────────────────────────────────────

function LineChart({ data, goal, completionDates, unit = '', emptyMsg, ariaLabel }) {
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

      {points.map((p, i) => (
        <g key={p.date}>
          <circle cx={p.x} cy={p.y} r={3.5} className={`chart-dot${p.completed ? ' chart-dot--completed' : ''}`}>
            <title>{`${p.date}: ${p.value.toFixed(1)}${unit}`}</title>
          </circle>
          {labelIndices.has(i) && (
            <text x={p.x} y={CHART_H - 4} className="chart-axis-label" textAnchor="middle">
              {new Date(p.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
            </text>
          )}
        </g>
      ))}
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

// ── Main page ─────────────────────────────────────────────────────────────────

function HabitBadges({ habits, formatGoal, auto = false }) {
  if (!habits.length) return null
  return (
    <div className="health-habits-row">
      {habits.map(h => (
        <span key={h.id} className="health-habit-badge">
          {h.name}
          {h.withings_goal != null && ` · ${formatGoal(h.withings_goal)}`}
          {auto && <span className="health-habit-badge-auto"> auto-checks</span>}
        </span>
      ))}
    </div>
  )
}

const KG_TO_LBS = 2.20462

export default function HealthPage({ habits = [], healthData, healthGoals, withingsConnected, onOpenSettings }) {
  const [isImperial, setIsImperial] = useState(() => localStorage.getItem('health-unit') === 'imperial')

  const toggleUnit = () => setIsImperial(v => {
    const next = !v
    localStorage.setItem('health-unit', next ? 'imperial' : 'metric')
    return next
  })

  const toDisplay = (kg) => isImperial ? Math.round(kg * KG_TO_LBS * 10) / 10 : kg
  const weightUnit = isImperial ? ' lbs' : ' kg'

  const measurements = healthData?.measurements ?? []
  const habitCompletions = healthData?.habit_completions ?? {}

  const stepsData    = measurements.filter(m => m.metric === 'steps')
  const fatData      = measurements.filter(m => m.metric === 'fat_ratio')
  const weightData   = measurements.filter(m => m.metric === 'weight')

  const stepsHabits  = habits.filter(h => h.withings_metric === 'steps'     && !h.archived)
  const fatHabits    = habits.filter(h => h.withings_metric === 'fat_ratio'  && !h.archived)
  const weightHabits = habits.filter(h => h.withings_metric === 'weight'     && !h.archived)

  const completions = (hs) => hs.flatMap(h => habitCompletions[String(h.id)] ?? [])

  // Habit goals take priority; standalone healthGoals are the fallback
  const primaryStepsGoal  = stepsHabits[0]?.withings_goal  ?? healthGoals?.steps     ?? null
  const primaryFatGoal    = fatHabits[0]?.withings_goal    ?? healthGoals?.fat_ratio  ?? null
  const primaryWeightGoal = weightHabits[0]?.withings_goal ?? healthGoals?.weight     ?? null

  return (
    <div className="health-page">
      <div className="health-page-header">
        <h2 className="health-page-title">Health</h2>
        <button className="health-page-settings-btn" onClick={onOpenSettings}>
          {withingsConnected ? 'Withings settings' : 'Connect Withings'}
        </button>
      </div>

      {!withingsConnected && (
        <div className="health-not-connected">
          <p>Connect your Withings account to start tracking steps, body fat, and weight.</p>
          <button className="btn-primary" onClick={onOpenSettings}>Connect Withings</button>
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
        <HabitBadges habits={stepsHabits} formatGoal={g => `${Math.round(g).toLocaleString()} steps`} auto />
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
        <HabitBadges habits={fatHabits} formatGoal={g => `goal ≤ ${g.toFixed(1)}%`} auto />
        <LineChart
          data={fatData} goal={primaryFatGoal} completionDates={completions(fatHabits)}
          unit="%" emptyMsg="No body fat data yet. Weigh in with your Withings scale." ariaLabel="Body fat % line chart"
        />
        <ChartLegend items={[
          { color: 'var(--color-week)', label: 'Body fat %' },
          ...(primaryFatGoal != null ? [{ color: '#f59e0b', label: 'Target' }] : []),
          ...(fatHabits.length > 0 ? [{ color: '#8b5cf6', label: 'Habit completed' }] : []),
        ]} />
      </section>

      {/* Weight */}
      <section className="health-section">
        <div className="health-section-header">
          <h3 className="health-section-title">Weight</h3>
          <div className="health-unit-toggle">
            <button className={`health-unit-btn${!isImperial ? ' health-unit-btn--active' : ''}`} onClick={() => isImperial && toggleUnit()}>kg</button>
            <button className={`health-unit-btn${isImperial ? ' health-unit-btn--active' : ''}`} onClick={() => !isImperial && toggleUnit()}>lbs</button>
          </div>
          {primaryWeightGoal != null && (
            <span className="health-section-goal">Goal: ≤ {toDisplay(primaryWeightGoal).toFixed(1)}{weightUnit}</span>
          )}
        </div>
        <HabitBadges habits={weightHabits} formatGoal={g => `goal ≤ ${toDisplay(g).toFixed(1)}${weightUnit}`} auto />
        <LineChart
          data={weightData.map(d => ({ ...d, value: toDisplay(d.value) }))}
          goal={primaryWeightGoal != null ? toDisplay(primaryWeightGoal) : null}
          completionDates={completions(weightHabits)}
          unit={weightUnit} emptyMsg="No weight data yet. Weigh in with your Withings scale." ariaLabel="Weight line chart"
        />
        <ChartLegend items={[
          { color: 'var(--color-week)', label: `Weight (${isImperial ? 'lbs' : 'kg'})` },
          ...(primaryWeightGoal != null ? [{ color: '#f59e0b', label: 'Target' }] : []),
          ...(weightHabits.length > 0 ? [{ color: '#8b5cf6', label: 'Habit completed' }] : []),
        ]} />
      </section>
    </div>
  )
}
