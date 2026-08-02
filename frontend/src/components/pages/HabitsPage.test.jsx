import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import HabitsPage from './HabitsPage'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, fetchHabitStreakDays: vi.fn(() => Promise.resolve([])) }
})

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
  best_streak: 3,
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

// ── Weekly tier badge ────────────────────────────────────────────────────────

describe('weekly tier badge', () => {
  it('shows no badge when fewer than 3 days completed this week', () => {
    const habit = { ...base, recent_completions: [true, true, false, false, false, false, false] }
    renderHabits([habit])
    expect(document.querySelector('.habit-card-tier')).toBeNull()
  })

  it('shows bronze badge for 3-4 days completed this week', () => {
    const habit = { ...base, recent_completions: [true, true, true, false, false, false, false] }
    renderHabits([habit])
    expect(document.querySelector('.habit-card-tier--bronze')).not.toBeNull()
  })

  it('shows silver badge for 5-6 days completed this week', () => {
    const habit = { ...base, recent_completions: [true, true, true, true, true, false, false] }
    renderHabits([habit])
    expect(document.querySelector('.habit-card-tier--silver')).not.toBeNull()
  })

  it('shows gold badge for 7/7 days completed this week', () => {
    const habit = { ...base, recent_completions: [true, true, true, true, true, true, true] }
    renderHabits([habit])
    expect(document.querySelector('.habit-card-tier--gold')).not.toBeNull()
  })
})

// ── Expandable heatmap ───────────────────────────────────────────────────────

describe('habit history heatmap', () => {
  it('is collapsed by default', () => {
    renderHabits([base])
    expect(document.querySelector('.habit-card-heatmap-wrap')).toBeNull()
  })

  it('clicking the history toggle expands the heatmap', async () => {
    renderHabits([base])
    fireEvent.click(screen.getByRole('button', { name: /toggle completion history/i }))
    await waitFor(() => {
      expect(document.querySelector('.habit-card-heatmap-wrap')).not.toBeNull()
    })
  })

  it('clicking the toggle again collapses the heatmap', async () => {
    renderHabits([base])
    const toggle = screen.getByRole('button', { name: /toggle completion history/i })
    fireEvent.click(toggle)
    await waitFor(() => {
      expect(document.querySelector('.habit-card-heatmap-wrap')).not.toBeNull()
    })
    fireEvent.click(toggle)
    expect(document.querySelector('.habit-card-heatmap-wrap')).toBeNull()
  })

  it('shows the best streak in the expanded panel', async () => {
    const habit = { ...base, best_streak: 21 }
    renderHabits([habit])
    fireEvent.click(screen.getByRole('button', { name: /toggle completion history/i }))
    await waitFor(() => {
      const best = document.querySelector('.habit-card-best')
      expect(best).not.toBeNull()
      expect(best.textContent).toMatch(/best streak: 21d/i)
    })
  })
})

// ── Habit tags ───────────────────────────────────────────────────────────────

describe('habit tags', () => {
  const workTag = { id: 1, name: 'work', color: '#3b82f6' }
  const personalTag = { id: 2, name: 'personal', color: '#10b981' }

  it('shows a tag chip for each tag on the habit', () => {
    const habit = { ...base, tags: [workTag] }
    renderHabits([habit])
    const pills = document.querySelectorAll('.habit-card-tag-pill')
    expect(pills.length).toBe(1)
    expect(pills[0].textContent).toBe('work')
  })

  it('shows no tag row when the habit has no tags', () => {
    renderHabits([base])
    expect(document.querySelectorAll('.habit-card-tag-pill').length).toBe(0)
  })

  it('filters habits by selectedTagIds', () => {
    const workHabit = { ...base, id: 1, name: 'Standup', tags: [workTag] }
    const personalHabit = { ...base, id: 2, name: 'Meditate', tags: [personalTag] }
    renderHabits([workHabit, personalHabit], { selectedTagIds: new Set([1]) })
    expect(document.body.textContent).toContain('Standup')
    expect(document.body.textContent).not.toContain('Meditate')
  })

  it('shows the union of matching habits for multiple selected tags', () => {
    const workHabit = { ...base, id: 1, name: 'Standup', tags: [workTag] }
    const personalHabit = { ...base, id: 2, name: 'Meditate', tags: [personalTag] }
    const untaggedHabit = { ...base, id: 3, name: 'Read', tags: [] }
    renderHabits([workHabit, personalHabit, untaggedHabit], { selectedTagIds: new Set([1, 2]) })
    expect(document.body.textContent).toContain('Standup')
    expect(document.body.textContent).toContain('Meditate')
    expect(document.body.textContent).not.toContain('Read')
  })

  it('shows the tag-specific empty state when no habit matches the filter', () => {
    renderHabits([{ ...base, tags: [] }], { selectedTagIds: new Set([999]) })
    expect(document.querySelector('.habits-empty').textContent).toMatch(/no habits with this tag/i)
  })
})
