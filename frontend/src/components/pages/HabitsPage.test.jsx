import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import HabitsPage from './HabitsPage'

afterEach(cleanup)

function renderHabits(habits, extra = {}) {
  const onToggle = extra.onToggle ?? vi.fn()
  render(
    <MemoryRouter>
      <HabitsPage
        habits={habits}
        archivedHabits={[]}
        allTags={[]}
        onToggle={onToggle}
        onAdd={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
        onArchive={vi.fn()}
        onUnarchive={vi.fn()}
        {...extra}
      />
    </MemoryRouter>
  )
  return onToggle
}

const base = {
  id: 1,
  name: 'Read 30 minutes',
  completed_today: false,
  streak: 3,
  tags: [],
  recent_completions: [false, false, false, false, false, false, false],
  withings_metric: null,
  withings_goal: null,
  is_experiment: false,
  archived: false,
}

// ── Regular habits ─────────────────────────────────────────────────────────────

describe('regular habit (no withings_metric, no is_experiment)', () => {
  it('renders an enabled check button', () => {
    renderHabits([base])
    const btn = screen.getByRole('button', { name: /mark complete/i })
    expect(btn).toBeTruthy()
    expect(btn.disabled).toBe(false)
  })

  it('clicking the button calls onToggle with the habit', () => {
    const onToggle = vi.fn()
    renderHabits([base], { onToggle })
    fireEvent.click(screen.getByRole('button', { name: /mark complete/i }))
    expect(onToggle).toHaveBeenCalledOnce()
    expect(onToggle).toHaveBeenCalledWith(base)
  })

  it('completed habit shows Mark incomplete button', () => {
    renderHabits([{ ...base, completed_today: true }])
    expect(screen.getByRole('button', { name: /mark incomplete/i })).toBeTruthy()
  })

  it('clicking a completed habit calls onToggle', () => {
    const onToggle = vi.fn()
    const done = { ...base, completed_today: true }
    renderHabits([done], { onToggle })
    fireEvent.click(screen.getByRole('button', { name: /mark incomplete/i }))
    expect(onToggle).toHaveBeenCalledWith(done)
  })
})

// ── Experiment habits (is_experiment=true, no withings_metric) ─────────────────

describe('experiment habit without Withings metric', () => {
  const exp = { ...base, id: 2, name: '1 hour screen-free time', is_experiment: true }

  it('renders an enabled check button (same as regular habit)', () => {
    renderHabits([exp])
    const btn = screen.getByRole('button', { name: /mark complete/i })
    expect(btn).toBeTruthy()
    expect(btn.disabled).toBe(false)
  })

  it('clicking the button calls onToggle', () => {
    const onToggle = vi.fn()
    renderHabits([exp], { onToggle })
    fireEvent.click(screen.getByRole('button', { name: /mark complete/i }))
    expect(onToggle).toHaveBeenCalledOnce()
    expect(onToggle).toHaveBeenCalledWith(exp)
  })

  it('does not apply the auto-sync dashed style', () => {
    renderHabits([exp])
    const btn = screen.getByRole('button', { name: /mark complete/i })
    expect(btn.className).not.toContain('--auto')
  })
})

// ── Withings-synced habits ─────────────────────────────────────────────────────

describe('Withings-synced habit (withings_metric set)', () => {
  const synced = { ...base, id: 3, name: '10,000 steps', withings_metric: 'steps', withings_goal: 10000 }

  it('check button is disabled', () => {
    renderHabits([synced])
    const btn = document.querySelector('.habit-card-check')
    expect(btn.disabled).toBe(true)
  })

  it('does not call onToggle when clicked', () => {
    const onToggle = vi.fn()
    renderHabits([synced], { onToggle })
    fireEvent.click(document.querySelector('.habit-card-check'))
    expect(onToggle).not.toHaveBeenCalled()
  })

  it('applies the auto-sync dashed style when not completed', () => {
    renderHabits([synced])
    const btn = document.querySelector('.habit-card-check')
    expect(btn.className).toContain('--auto')
  })
})
