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

// ── Steps bar chart ───────────────────────────────────────────────────────────

function StepsChart({ data, goal, completionDates, habitName }) {
  if (!data.length) return <div className="health-chart-empty">No step data yet. Sync your Withings account.</div>

  const last30 = data.slice(-30)
  const maxSteps = Math.max(...last30.map(d => d.value), goal ?? 0, 1)
  const minSteps = 0
  const barW = Math.max(4, (INNER_W / last30.length) - 2)
  const completionSet = new Set(completionDates ?? [])

  // X-axis: show ~6 date labels
  const labelIndices = new Set()
  const step = Math.max(1, Math.floor((last30.length - 1) / 5))
  for (let i = 0; i < last30.length; i += step) labelIndices.add(i)
  labelIndices.add(last30.length - 1)

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      className="health-chart-svg"
      aria-label="Steps bar chart"
    >
      <AxisY min={minSteps} max={maxSteps} ticks={4} />

      {/* Goal line */}
      {goal != null && (
        <line
          x1={PAD.left} x2={PAD.left + INNER_W}
          y1={yScale(goal, minSteps, maxSteps)}
          y2={yScale(goal, minSteps, maxSteps)}
          className="chart-goal-line"
        />
      )}

      {last30.map((d, i) => {
        const barH = Math.max(1, yScale(minSteps, minSteps, maxSteps) - yScale(d.value, minSteps, maxSteps))
        const x = PAD.left + (INNER_W / last30.length) * i + (INNER_W / last30.length - barW) / 2
        const y = yScale(d.value, minSteps, maxSteps)
        const met = goal != null ? d.value >= goal : false
        const completed = completionSet.has(d.date)
        const showLabel = labelIndices.has(i)
        return (
          <g key={d.date}>
            <rect
              x={x} y={y} width={barW} height={barH}
              className={`chart-bar ${met ? 'chart-bar--met' : ''}`}
            >
              <title>{`${d.date}: ${Math.round(d.value).toLocaleString()} steps`}</title>
            </rect>
            {completed && (
              <circle
                cx={x + barW / 2}
                cy={yScale(minSteps, minSteps, maxSteps) + 10}
                r={3}
                className="chart-completion-dot"
              />
            )}
            {showLabel && (
              <text
                x={x + barW / 2}
                y={CHART_H - 4}
                className="chart-axis-label"
                textAnchor="middle"
              >
                {new Date(d.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

// ── Body fat line chart ───────────────────────────────────────────────────────

function FatChart({ data, goal, completionDates }) {
  if (!data.length) return <div className="health-chart-empty">No body fat data yet. Weigh in with your Withings scale to start tracking.</div>

  const minFat = Math.floor(Math.min(...data.map(d => d.value), goal ?? Infinity) - 1)
  const maxFat = Math.ceil(Math.max(...data.map(d => d.value), goal ?? -Infinity) + 1)
  const completionSet = new Set(completionDates ?? [])

  const points = data.map((d, i) => ({
    x: xPos(i, data.length),
    y: yScale(d.value, minFat, maxFat),
    date: d.date,
    value: d.value,
    completed: completionSet.has(d.date),
  }))

  const polyline = points.map(p => `${p.x},${p.y}`).join(' ')

  // Label indices
  const labelIndices = new Set()
  const step = Math.max(1, Math.floor((data.length - 1) / 5))
  for (let i = 0; i < data.length; i += step) labelIndices.add(i)
  labelIndices.add(data.length - 1)

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      className="health-chart-svg"
      aria-label="Body fat line chart"
    >
      <AxisY min={minFat} max={maxFat} ticks={4} />

      {/* Goal line */}
      {goal != null && (
        <line
          x1={PAD.left} x2={PAD.left + INNER_W}
          y1={yScale(goal, minFat, maxFat)}
          y2={yScale(goal, minFat, maxFat)}
          className="chart-goal-line"
        />
      )}

      {/* Area fill */}
      {points.length > 1 && (
        <polygon
          points={[
            `${points[0].x},${yScale(minFat, minFat, maxFat)}`,
            ...points.map(p => `${p.x},${p.y}`),
            `${points[points.length - 1].x},${yScale(minFat, minFat, maxFat)}`,
          ].join(' ')}
          className="chart-area"
        />
      )}

      <polyline points={polyline} className="chart-line" />

      {points.map((p, i) => (
        <g key={p.date}>
          <circle cx={p.x} cy={p.y} r={3.5} className={`chart-dot ${p.completed ? 'chart-dot--completed' : ''}`}>
            <title>{`${p.date}: ${p.value.toFixed(1)}%`}</title>
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

export default function HealthPage({ habits = [], healthData, withingsConnected, onOpenSettings }) {
  const measurements = healthData?.measurements ?? []
  const habitCompletions = healthData?.habit_completions ?? {}

  const stepsData = measurements.filter(m => m.metric === 'steps')
  const fatData = measurements.filter(m => m.metric === 'fat_ratio')

  // Find linked habits
  const stepsHabits = habits.filter(h => h.withings_metric === 'steps' && !h.archived)
  const fatHabits = habits.filter(h => h.withings_metric === 'fat_ratio' && !h.archived)

  // Aggregate completions across all linked habits per metric
  const stepCompletions = stepsHabits.flatMap(h => habitCompletions[String(h.id)] ?? [])
  const fatCompletions = fatHabits.flatMap(h => habitCompletions[String(h.id)] ?? [])

  const primaryStepsGoal = stepsHabits[0]?.withings_goal ?? null
  const primaryFatGoal = fatHabits[0]?.withings_goal ?? null

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
          <p>Connect your Withings account to start tracking step count and body fat percentage.</p>
          <button className="btn-primary" onClick={onOpenSettings}>Connect Withings</button>
        </div>
      )}

      {/* Steps */}
      <section className="health-section">
        <div className="health-section-header">
          <h3 className="health-section-title">Steps</h3>
          {primaryStepsGoal != null && (
            <span className="health-section-goal">Goal: {Math.round(primaryStepsGoal).toLocaleString()} steps/day</span>
          )}
        </div>
        {stepsHabits.length > 0 && (
          <div className="health-habits-row">
            {stepsHabits.map(h => (
              <span key={h.id} className="health-habit-badge">
                {h.name}
                {h.withings_goal != null && ` · ${Math.round(h.withings_goal).toLocaleString()} steps`}
                <span className="health-habit-badge-auto"> auto-checks</span>
              </span>
            ))}
          </div>
        )}
        <StepsChart
          data={stepsData}
          goal={primaryStepsGoal}
          completionDates={stepCompletions}
        />
        <ChartLegend items={[
          { color: 'var(--color-today)', label: 'Steps' },
          { color: '#22c55e', label: 'Goal met' },
          ...(primaryStepsGoal != null ? [{ color: '#f59e0b', label: 'Daily goal' }] : []),
          ...(stepsHabits.length > 0 ? [{ color: '#8b5cf6', label: 'Habit completed' }] : []),
        ]} />
      </section>

      {/* Body fat */}
      <section className="health-section">
        <div className="health-section-header">
          <h3 className="health-section-title">Body Fat %</h3>
          {primaryFatGoal != null && (
            <span className="health-section-goal">Goal: {primaryFatGoal.toFixed(1)}%</span>
          )}
        </div>
        {fatHabits.length > 0 && (
          <div className="health-habits-row">
            {fatHabits.map(h => (
              <span key={h.id} className="health-habit-badge">
                {h.name}
                {h.withings_goal != null && ` · goal ${h.withings_goal.toFixed(1)}%`}
              </span>
            ))}
          </div>
        )}
        <FatChart
          data={fatData}
          goal={primaryFatGoal}
          completionDates={fatCompletions}
        />
        <ChartLegend items={[
          { color: 'var(--color-week)', label: 'Body fat %' },
          ...(primaryFatGoal != null ? [{ color: '#f59e0b', label: 'Target' }] : []),
          ...(fatHabits.length > 0 ? [{ color: '#8b5cf6', label: 'Habit completed' }] : []),
        ]} />
      </section>
    </div>
  )
}
