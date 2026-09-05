/**
 * Functional regression tests.
 *
 * Strategy:
 *   - All API calls are mocked — no backend required.
 *   - Tests assert that key elements are visible, not pixel-identical.
 *   - This avoids screenshot fragility (font rendering, video frames, OS differences)
 *     while still catching the regressions that matter: missing buttons, broken
 *     navigation, disappeared sections, broken modals.
 */

import { test, expect } from '@playwright/test'

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------
const TAGS = [
  { id: 1, name: 'work',     color: '#3b82f6' },
  { id: 2, name: 'personal', color: '#10b981' },
]

const TODOS = [
  {
    id: 1, title: 'Daily Engineering Standup', section: 'today', completed: false,
    scheduled_at: '2026-06-03T09:00:00', description: null, position: 0, overdue_days: 0,
    tags: [{ id: 1, name: 'work', color: '#3b82f6' }],
  },
  {
    id: 2, title: 'Review pull requests', section: 'today', completed: false,
    scheduled_at: null, description: null, position: 1, overdue_days: 0,
    tags: [{ id: 1, name: 'work', color: '#3b82f6' }],
  },
  {
    id: 3, title: 'Call dentist', section: 'today', completed: false,
    scheduled_at: null, description: null, position: 2, overdue_days: 0, tags: [],
  },
  {
    id: 4, title: 'Finish quarterly report', section: 'week', completed: false,
    scheduled_at: null, description: null, position: 3, overdue_days: 0,
    tags: [{ id: 1, name: 'work', color: '#3b82f6' }],
  },
  {
    id: 5, title: 'Book conference flights', section: 'month', completed: false,
    scheduled_at: null, description: null, position: 4, overdue_days: 0, tags: [],
  },
  {
    id: 6, title: 'Read that article', section: 'later', completed: false,
    scheduled_at: null, description: null, position: 5, overdue_days: 0,
    tags: [{ id: 2, name: 'personal', color: '#10b981' }],
  },
]

const HABITS = [
  {
    id: 1, name: 'Morning meditation', completed_today: true, streak: 7, best_streak: 14,
    tags: [], recurrence_rule: 'daily',
    recent_completions: [true, true, true, true, false, true, true],
    withings_metric: null, is_experiment: false,
  },
  {
    id: 2, name: 'Evening walk', completed_today: false, streak: 3, best_streak: 3,
    tags: [{ id: 2, name: 'personal', color: '#10b981' }], recurrence_rule: 'daily',
    recent_completions: [false, false, false, false, true, true, false],
    withings_metric: null, is_experiment: false,
  },
  {
    id: 3, name: '🧪 1 hour screen-free time', completed_today: false, streak: 0, best_streak: 0,
    tags: [], recurrence_rule: 'daily',
    recent_completions: [false, false, false, false, false, false, false],
    withings_metric: null, is_experiment: true,
  },
]

const CALENDAR_EVENTS = [
  {
    id: 'ev1', title: 'Product Review', section: 'today',
    start: '2026-06-03T14:00:00', end: '2026-06-03T15:00:00',
    all_day: false, description: 'Weekly product review', location: 'Conference Room B',
  },
]

const CARDS = [
  {
    id: 7, title: 'Shopping list',
    description: 'Milk\nEggs\nBread\nCoffee',
    section: 'later', completed: false, archived: false, position: 0,
    tags: [{ id: 2, name: 'personal', color: '#10b981' }],
    updated_at: '2026-06-03T08:00:00Z', created_at: '2026-06-03T08:00:00Z',
  },
  {
    id: 8, title: 'Sprint ideas',
    description: 'Next sprint candidates:\n\nImprove search\nAdd dark mode option\nPerformance pass',
    section: 'later', completed: false, archived: false, position: 1,
    tags: [{ id: 1, name: 'work', color: '#3b82f6' }],
    updated_at: '2026-06-02T16:00:00Z', created_at: '2026-06-02T16:00:00Z',
  },
]

const ALL_TODOS = [...TODOS, ...CARDS]

// ---------------------------------------------------------------------------
// Shared setup
// ---------------------------------------------------------------------------
async function mockAPIs(page) {
  await page.route('**/api/auth/check', r =>
    r.fulfill({ json: { authed: true, enabled: false } }))

  await page.route('**/api/cards', r => r.fulfill({ json: ALL_TODOS }))
  await page.route('**/api/tags', r => r.fulfill({ json: TAGS }))
  await page.route('**/api/calendar-events', r => r.fulfill({ json: CALENDAR_EVENTS }))
  await page.route('**/api/calendar-mappings', r => r.fulfill({ json: [] }))
  await page.route('**/api/engineering/items', r => r.fulfill({ json: [] }))
  await page.route('**/api/engineering/sync', r => r.fulfill({ json: { created: 0, closed: 0, skipped: 0, cards_created: 0, error: null } }))
  await page.route('**/api/engineering/config', r => r.fulfill({ json: { configured: false, repos: [] } }))
  await page.route('**/api/engineering/status-config', r => r.fulfill({ json: {} }))
  await page.route('**/api/engineering/repo-tags', r => r.fulfill({ json: {} }))
  await page.route('**/api/bridge/install-token', r => r.fulfill({ json: { token: 'test-install-token' } }))
  await page.route('**/api/bridge/jobs/status', r => r.fulfill({ json: { statuses: {} } }))
  await page.route('**/api/bridge/jobs/dashboard', r => r.fulfill({ json: { jobs: [] } }))
  await page.route('**/api/bridge/jobs/card/*/history', r => r.fulfill({ json: { jobs: [] } }))
  await page.route('**/api/bridge/checkpoint-patterns', r => {
    if (r.request().method() === 'PUT') return r.fulfill({ json: { ok: true } })
    return r.fulfill({ json: { patterns: [] } })
  })
  await page.route('**/api/settings/navigation', r =>
    r.fulfill({ json: { order: ['today', 'board', 'calendar', 'health', 'engineering'], default_page: 'today' } }))

  await page.route('**/api/cards/*/thread/context-from', r =>
    r.fulfill({ json: { context_text: '### Today\n- Buy milk\n- Call dentist', label: 'Today', count: 2 } }))
  await page.route('**/api/cards/*/thread', r => r.fulfill({ json: { messages: [], context: null, output: null } }))
  // habits: handle both active and archived requests
  await page.route(/\/api\/habits(\?|$)/, r => {
    const url = r.request().url()
    return r.fulfill({ json: url.includes('archived=true') ? [] : HABITS })
  })
  await page.route(/\/api\/habits\/\d+\/streak-days/, r => r.fulfill({ json: [] }))
  await page.route('**/api/trip', r => r.fulfill({ json: null }))

  await page.route('**/api/insights', r => r.fulfill({ json: [] }))

  await page.route('**/api/discovery/feeds', r => r.fulfill({ json: [] }))
  await page.route('**/api/discovery/interests', r => r.fulfill({ json: { interests: '' } }))
  await page.route('**/api/discovery/events', r => r.fulfill({ json: [] }))
  await page.route('**/api/discovery/feedback', r => r.fulfill({ json: [] }))

  await page.route('**/api/withings/status', r =>
    r.fulfill({ json: { connected: false, last_synced: null } }))
  await page.route('**/api/withings/goals', r =>
    r.fulfill({ json: { steps: null, fat_ratio: null, weight: null } }))
  await page.route('**/api/withings/health-data**', r =>
    r.fulfill({ json: { measurements: [], habit_completions: {} } }))
  await page.route('**/api/health/measurements**', r => {
    if (r.request().method() === 'POST') {
      return r.fulfill({ status: 201, json: { id: 1, ...r.request().postDataJSON(), source: 'manual' } })
    }
    return r.fulfill({ json: { ok: true } })
  })

  // Briefing SSE: send weather + text then close
  await page.route('**/api/briefing**', r =>
    r.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body:
        'data: {"type":"weather","emojis":"⛅","high":72,"low":58}\n\n' +
        'data: {"section":"today","text":"A productive day ahead."}\n\n' +
        'data: [DONE]\n\n',
    }))

  // Standalone weather endpoint — registered after the broader briefing** glob so it takes priority
  await page.route('**/api/briefing/weather**', r =>
    r.fulfill({ json: { emojis: '⛅', high: 72, low: 58, description: 'partly cloudy', windy: false, umbrella: false, snow: false, cold: false } }))
}

async function waitForApp(page) {
  await page.waitForSelector('.app-header', { state: 'visible' })
  // Wait for briefing text to appear — this implies weather data was also received.
  // On pages without a briefing the selector never matches and the catch is a no-op.
  await page.waitForSelector('.briefing-text', { state: 'visible', timeout: 8000 }).catch(() => {})
  await page.waitForTimeout(200)
}

test.beforeEach(async ({ page }) => {
  await page.clock.setSystemTime(new Date('2026-06-03T10:00:00'))
  await page.addInitScript(() => localStorage.clear())
  // Briefing auto-show defaults to off (collapsed on load) -- opt every test into the old
  // auto-generate behavior by default, since only the dedicated "briefing auto-show setting"
  // tests below are actually about that default; everywhere else just wants the briefing
  // content available without an 8s wait for a click that never happens.
  await page.addInitScript(() => localStorage.setItem('briefing-auto-show', 'true'))
  await mockAPIs(page)
})

// ---------------------------------------------------------------------------
// App shell — present on every page
// ---------------------------------------------------------------------------
test.describe('app shell', () => {
  test('header and nav are visible', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)

    // Header
    await expect(page.getByRole('button', { name: /capture/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /search/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /settings/i })).toBeVisible()

    // Sidebar nav (desktop) or mobile nav
    for (const label of ['Today', 'Board', 'Calendar', 'Habits', 'Engineering']) {
      await expect(page.getByRole('button', { name: label }).or(page.getByText(label)).first()).toBeVisible()
    }
  })
})

// ---------------------------------------------------------------------------
// Assist step (within quick-add modal)
// ---------------------------------------------------------------------------
test.describe('assist step', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
  })

  test('pressing A key opens modal in assist step', async ({ page }) => {
    await page.keyboard.press('a')
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('heading', { name: /✦ assist/i })).toBeVisible()
  })

  test('clicking ✦ Assist in quick modal switches to assist step', async ({ page }) => {
    await page.locator('button.btn-primary').first().click()
    await page.locator('.btn-assist').click()
    await expect(page.getByRole('heading', { name: /✦ assist/i })).toBeVisible()
  })

  test('assist step has context selector and prompt textarea', async ({ page }) => {
    await page.keyboard.press('a')
    await expect(page.locator('#qa-assist-context')).toBeVisible()
    await expect(page.locator('.quick-assist-prompt')).toBeVisible()
  })

  test('Generate button is disabled when prompt is empty', async ({ page }) => {
    await page.locator('button.btn-primary').first().click()
    await page.locator('.btn-assist').click()
    await expect(page.getByRole('button', { name: /generate/i })).toBeDisabled()
  })

  test('Back button returns to input step', async ({ page }) => {
    await page.keyboard.press('a')
    await expect(page.getByRole('heading', { name: /✦ assist/i })).toBeVisible()
    await page.getByRole('button', { name: /back/i }).click()
    await expect(page.getByRole('button', { name: /continue/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Today page
// ---------------------------------------------------------------------------
test.describe('today page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
  })

  test('date heading', async ({ page }) => {
    await expect(page.getByText('Wednesday, June 3')).toBeVisible()
  })

  test('briefing text is visible', async ({ page }) => {
    await expect(page.locator('.briefing-text')).toBeVisible()
    await expect(page.getByText('A productive day ahead.')).toBeVisible()
  })

  test('habits are visible in their own section', async ({ page }) => {
    await expect(page.getByText('Morning meditation')).toBeVisible()
    await expect(page.getByText('Evening walk')).toBeVisible()
  })

  test('habits section appears before schedule section', async ({ page }) => {
    // Habits sit right under Insights, ahead of Schedule, so they're
    // checkable off without scrolling past the day's task/event list first.
    const titles = await page.locator('.today-section-title-text').allTextContents()
    const habitsIdx   = titles.findIndex(t => t.includes('Habits'))
    const scheduleIdx = titles.findIndex(t => t.includes('Schedule'))
    expect(habitsIdx).toBeGreaterThanOrEqual(0)
    expect(scheduleIdx).toBeGreaterThanOrEqual(0)
    expect(habitsIdx).toBeLessThan(scheduleIdx)
  })

  test('schedule section with mocked tasks and event', async ({ page }) => {
    await expect(page.getByText('Daily Engineering Standup')).toBeVisible()
    // Scope to main content so the sidebar's duplicate doesn't cause strict-mode violation
    await expect(page.locator('main').getByText('Product Review')).toBeVisible()
  })

  test('unscheduled today items appear in schedule section', async ({ page }) => {
    await expect(page.getByText('Review pull requests').first()).toBeVisible()
    await expect(page.getByText('Call dentist')).toBeVisible()
  })

  test('highest-priority untimed task is marked "Up next" within the schedule list', async ({ page }) => {
    const focusCard = page.locator('.event-card--focus', { hasText: 'Review pull requests' })
    await expect(focusCard).toBeVisible()
    await expect(focusCard.getByText('Up next')).toBeVisible()
    // It appears exactly once — not duplicated in a separate banner
    await expect(page.getByText('Review pull requests')).toHaveCount(1)
  })
})

// ---------------------------------------------------------------------------
// Briefing auto-show setting
// ---------------------------------------------------------------------------
test.describe('briefing auto-show setting', () => {
  // The top-level beforeEach opts every test into auto-show=true for convenience (see its
  // comment) -- these tests are specifically about the real default, so override back to the
  // unset/off state before navigating.
  test('collapsed by default (auto-show off) renders a click-to-expand row instead of auto-fetching', async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem('briefing-auto-show'))
    await page.goto('/today')
    await page.waitForSelector('.app-header', { state: 'visible' })
    await expect(page.locator('.briefing--collapsed')).toBeVisible()
    await expect(page.locator('.briefing-text')).toHaveCount(0)
  })

  test('clicking the collapsed briefing expands it and fetches the content', async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem('briefing-auto-show'))
    await page.goto('/today')
    await page.waitForSelector('.app-header', { state: 'visible' })
    await page.locator('.briefing--collapsed').click()
    await expect(page.locator('.briefing-text')).toBeVisible()
    await expect(page.getByText('A productive day ahead.')).toBeVisible()
  })

  test('auto-show on fetches the briefing immediately on load', async ({ page }) => {
    // Already true via the top-level beforeEach -- confirms the enabled path explicitly.
    await page.goto('/today')
    await page.waitForSelector('.app-header', { state: 'visible' })
    await expect(page.locator('.briefing-text')).toBeVisible()
    await expect(page.locator('.briefing--collapsed')).toHaveCount(0)
  })

  test('toggling the setting off in the settings menu persists to localStorage', async ({ page }) => {
    await page.goto('/today')
    await page.waitForSelector('.app-header', { state: 'visible' })
    await page.locator('button[title="Settings"]').click()
    await page.locator('.settings-dropdown-item', { hasText: /show briefing automatically/i }).click()
    await expect.poll(() => page.evaluate(() => localStorage.getItem('briefing-auto-show'))).toBe('false')
  })
})

// ---------------------------------------------------------------------------
// Tasks board
// ---------------------------------------------------------------------------
test.describe('tasks board', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('board columns are visible', async ({ page }) => {
    for (const col of ['Today', 'This Week', 'This Month', 'Later']) {
      await expect(page.locator('.column-label', { hasText: col })).toBeVisible()
    }
  })

  test('tasks appear in correct columns', async ({ page }) => {
    await expect(page.getByText('Daily Engineering Standup')).toBeVisible()
    await expect(page.getByText('Finish quarterly report')).toBeVisible()
    await expect(page.getByText('Book conference flights')).toBeVisible()
    await expect(page.getByText('Read that article')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Bridge job status badge on card tiles (Board + Today)
// ---------------------------------------------------------------------------
test.describe('bridge job status badge', () => {
  // Card id 1 = "Daily Engineering Standup" (section: today) -- appears on both
  // /board's Today column and the Today page's schedule list.

  test('no badge appears for a card with no bridge job', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    const card = page.locator('.event-card', { hasText: 'Daily Engineering Standup' })
    await expect(card.locator('.card-bridge-dot')).toHaveCount(0)
  })

  test('shows a running badge for a card with a running job', async ({ page }) => {
    await page.route('**/api/bridge/jobs/status', r => r.fulfill({
      json: { statuses: { '1': { job_id: 10, status: 'running' } } },
    }))
    await page.goto('/board')
    await waitForApp(page)

    const card = page.locator('.event-card', { hasText: 'Daily Engineering Standup' })
    const dot = card.locator('.card-bridge-dot')
    await expect(dot).toHaveCount(1)
    await expect(dot).toHaveClass(/card-bridge-dot--running/)
    await expect(dot).toHaveAttribute('title', /running/i)
  })

  test('shows an error badge for a card with an errored job', async ({ page }) => {
    await page.route('**/api/bridge/jobs/status', r => r.fulfill({
      json: { statuses: { '1': { job_id: 10, status: 'error' } } },
    }))
    await page.goto('/board')
    await waitForApp(page)

    const dot = page.locator('.event-card', { hasText: 'Daily Engineering Standup' }).locator('.card-bridge-dot')
    await expect(dot).toHaveClass(/card-bridge-dot--error/)
    await expect(dot).toHaveAttribute('title', /errored/i)
  })

  test('shows a done badge for a card with a completed job', async ({ page }) => {
    await page.route('**/api/bridge/jobs/status', r => r.fulfill({
      json: { statuses: { '1': { job_id: 10, status: 'done' } } },
    }))
    await page.goto('/board')
    await waitForApp(page)

    const dot = page.locator('.event-card', { hasText: 'Daily Engineering Standup' }).locator('.card-bridge-dot')
    await expect(dot).toHaveClass(/card-bridge-dot--done/)
  })

  test('only the card with a job shows a badge, not its column neighbors', async ({ page }) => {
    await page.route('**/api/bridge/jobs/status', r => r.fulfill({
      json: { statuses: { '1': { job_id: 10, status: 'running' } } },
    }))
    await page.goto('/board')
    await waitForApp(page)

    const otherCard = page.locator('.event-card', { hasText: 'Review pull requests' })
    await expect(otherCard.locator('.card-bridge-dot')).toHaveCount(0)
  })

  test('badge also appears on the Today page for the same card', async ({ page }) => {
    await page.route('**/api/bridge/jobs/status', r => r.fulfill({
      json: { statuses: { '1': { job_id: 10, status: 'stalled' } } },
    }))
    await page.goto('/today')
    await waitForApp(page)

    const dot = page.locator('.event-card', { hasText: 'Daily Engineering Standup' }).locator('.card-bridge-dot')
    await expect(dot).toHaveClass(/card-bridge-dot--stalled/)
  })
})

// ---------------------------------------------------------------------------
// Later column on /board
// ---------------------------------------------------------------------------
test.describe('cards page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('Later column is visible', async ({ page }) => {
    await expect(page.locator('.column-label', { hasText: 'Later' })).toBeVisible()
  })

  test('cards with section=later appear in the Later column', async ({ page }) => {
    await expect(page.getByText('Shopping list')).toBeVisible()
    await expect(page.getByText('Sprint ideas')).toBeVisible()
  })

  test('clicking a card opens the detail panel and Edit shows edit form', async ({ page }) => {
    const card = page.locator('.event-card', { hasText: 'Shopping list' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await expect(panel).toBeVisible()
    const editBtn = panel.getByRole('button', { name: /^edit$/i })
    await expect(editBtn).toBeVisible()
    await editBtn.click()
    await expect(panel.locator('.cdp-title')).toHaveText(/edit card/i)
    await expect(page.locator('#cdp-title')).toBeVisible()
    await expect(page.locator('#cdp-desc')).toBeVisible()
    await expect(panel.getByRole('button', { name: /cancel/i })).toBeVisible()
    await expect(panel.getByRole('button', { name: /^save$/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Habits (now embedded in Health page)
// ---------------------------------------------------------------------------
test.describe('habits page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/health')
    await waitForApp(page)
  })

  test('habits section heading is visible', async ({ page }) => {
    await expect(page.locator('.habits-page-title')).toBeVisible()
  })

  test('habit cards are rendered', async ({ page }) => {
    await expect(page.getByText('Morning meditation')).toBeVisible()
    await expect(page.getByText('Evening walk')).toBeVisible()
  })

  test('completion toggle buttons are present', async ({ page }) => {
    await expect(page.getByRole('button', { name: /mark incomplete/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /mark complete/i }).first()).toBeVisible()
  })

  test('experiment habit (is_experiment=true, no withings_metric) has an enabled check button', async ({ page }) => {
    const btn = page.locator('.habit-card', { hasText: '🧪 1 hour screen-free time' })
      .getByRole('button', { name: /mark complete/i })
    await expect(btn).toBeVisible()
    await expect(btn).toBeEnabled()
  })

  test('experiment habit check button does not have the auto-sync dashed style', async ({ page }) => {
    const btn = page.locator('.habit-card', { hasText: '🧪 1 hour screen-free time' })
      .getByRole('button', { name: /mark complete/i })
    await expect(btn).not.toHaveClass(/check--auto/)
  })

  test('edit button is present on habit cards', async ({ page }) => {
    await expect(page.getByRole('button', { name: /edit habit/i }).first()).toBeVisible()
  })

  test('archive button is present on each habit card', async ({ page }) => {
    await expect(page.getByRole('button', { name: /archive habit/i }).first()).toBeVisible()
  })

  test('clicking the history toggle expands the completion heatmap', async ({ page }) => {
    const card = page.locator('.habit-card', { hasText: 'Morning meditation' })
    await card.getByRole('button', { name: /toggle completion history/i }).click()
    await expect(card.locator('.habit-card-heatmap-wrap')).toBeVisible()
    await expect(card.getByText(/best streak: 14d/i)).toBeVisible()
  })

  test('a weekly tier badge is shown for a habit with a strong completion week', async ({ page }) => {
    const card = page.locator('.habit-card', { hasText: 'Morning meditation' })
    await expect(card.locator('.habit-card-tier')).toBeVisible()
  })

  test('habit archive section is hidden when empty', async ({ page }) => {
    // No archived habits in mock → section should not render
    await expect(page.locator('.habits-archive')).toHaveCount(0)
  })

  test('editing a habit shows a tag picker and saves the selected tags', async ({ page }) => {
    let putBody = null
    await page.route('**/api/habits/1', (r) => {
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: { ...HABITS[0], tags: [{ id: 1, name: 'work', color: '#3b82f6' }] } })
      }
      return r.continue()
    })
    await page.locator('.habit-card', { hasText: 'Morning meditation' })
      .getByRole('button', { name: /edit habit/i }).click()
    // Re-scope by the now-open edit form rather than the habit name text --
    // that text is replaced by an <input> value (not textContent) once editing starts.
    const editingCard = page.locator('.habit-card', { has: page.locator('.habit-card-edit-form') })
    await expect(editingCard.locator('.tag-input')).toBeVisible()
    await editingCard.locator('.tag-input-text').fill('work')
    await editingCard.locator('.tag-input-option', { hasText: 'work' }).click()
    await expect(editingCard.locator('.tag-chip', { hasText: 'work' })).toBeVisible()
    await Promise.all([
      page.waitForResponse('**/api/habits/1'),
      editingCard.getByRole('button', { name: /save/i }).click(),
    ])
    expect(putBody.tag_ids).toEqual([1])
  })

  test('"+ New" button is visible in the header', async ({ page }) => {
    await expect(page.locator('.habits-add-btn')).toBeVisible()
  })

  test('clicking "+ New" reveals inline add form', async ({ page }) => {
    await page.locator('.habits-add-btn').click()
    await expect(page.locator('.habits-new-input')).toBeVisible()
    await expect(page.locator('.habits-new-save')).toBeVisible()
    await expect(page.locator('.habits-new-cancel')).toBeVisible()
  })

  test('inline add form is dismissed on Cancel', async ({ page }) => {
    await page.locator('.habits-add-btn').click()
    await expect(page.locator('.habits-new-input')).toBeVisible()
    await page.locator('.habits-new-cancel').click()
    await expect(page.locator('.habits-new-input')).toHaveCount(0)
  })
})

// ---------------------------------------------------------------------------
// Quick-add modal (mobile viewport)
// ---------------------------------------------------------------------------
test.describe('quick-add modal', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('opens and shows input', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await expect(page.locator('.quick-modal')).toBeVisible()
    await expect(page.getByRole('textbox')).toBeVisible()
  })

  test('has Cancel, Continue, and Assist footer buttons, no X button', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await expect(page.getByRole('button', { name: /cancel/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /continue/i })).toBeVisible()
    await expect(page.locator('.btn-assist')).toBeVisible()
    await expect(page.locator('.modal-close-btn')).toHaveCount(0)
  })

  test('confirm screen shows detected type tabs and Back/Add buttons', async ({ page }) => {
    await page.route('**/api/cards/parse-bulk', r =>
      r.fulfill({ json: { items: [{
        type: 'task', title: 'Call dentist', description: null,
        section: 'week', scheduled_at: null, suggested_tags: [], recurrence_rule: null,
      }]}}))
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await page.getByRole('textbox').fill('Call dentist next week')
    await page.getByRole('button', { name: /continue/i }).click()
    await expect(page.locator('.quick-type-tab--active')).toHaveText('Task')
    await expect(page.locator('.quick-type-tabs')).toBeVisible()
    await expect(page.getByRole('button', { name: /back/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /add task/i })).toBeVisible()
  })

  test('confirm screen type can be overridden', async ({ page }) => {
    await page.route('**/api/cards/parse-bulk', r =>
      r.fulfill({ json: { items: [{
        type: 'task', title: 'Morning run', description: null,
        section: 'today', scheduled_at: null, suggested_tags: [], recurrence_rule: null,
      }]}}))
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await page.getByRole('textbox').fill('Morning run every day')
    await page.getByRole('button', { name: /continue/i }).click()
    await expect(page.locator('.quick-type-tab--active')).toHaveText('Task')
    await page.locator('.quick-type-tab', { hasText: 'Habit' }).click()
    await expect(page.locator('.quick-type-tab--active')).toHaveText('Habit')
    await expect(page.getByRole('button', { name: /add habit/i })).toBeVisible()
  })

  test('confirm screen pre-selects suggested tags for a detected habit', async ({ page }) => {
    await page.route('**/api/cards/parse-bulk', r =>
      r.fulfill({ json: { items: [{
        type: 'habit', title: 'Standup', description: null,
        section: null, scheduled_at: null, suggested_tags: ['work'], recurrence_rule: null,
      }]}}))
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await page.getByRole('textbox').fill('daily standup')
    await page.getByRole('button', { name: /continue/i }).click()
    await expect(page.locator('.quick-type-tab--active')).toHaveText('Habit')
    await expect(page.locator('.tag-chip', { hasText: 'work' })).toBeVisible()
  })

  test('confirm screen Back returns to input', async ({ page }) => {
    await page.route('**/api/cards/parse-bulk', r =>
      r.fulfill({ json: { items: [{
        type: 'task', title: 'Grocery list', description: 'milk, eggs',
        section: 'none', scheduled_at: null, suggested_tags: [], recurrence_rule: null,
      }]}}))
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await page.getByRole('textbox').fill('grocery list: milk eggs')
    await page.getByRole('button', { name: /continue/i }).click()
    await expect(page.locator('.quick-type-tab--active')).toHaveText('Task')
    await page.getByRole('button', { name: /back/i }).click()
    await expect(page.getByRole('button', { name: /continue/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Mobile header layout
// ---------------------------------------------------------------------------
test.describe('mobile header layout', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('weather widget is left-aligned on mobile', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)

    const weather = page.locator('.header-weather')
    await expect(weather).toBeVisible()

    // Content must start in the left half of the viewport
    const box = await weather.boundingBox()
    const viewportWidth = 390
    expect(box.x).toBeLessThan(viewportWidth / 2)
    // And the left edge should be near the screen edge (within 32px of left padding)
    expect(box.x).toBeLessThan(32)
  })

  test('header is visible and usable on mobile', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)

    const header = page.locator('.app-header')
    await expect(header).toBeVisible()

    // Header must not be zero-height
    const box = await header.boundingBox()
    expect(box.height).toBeGreaterThan(40)

    // Action buttons must be reachable (not hidden behind notch area)
    await expect(page.getByRole('button', { name: /capture/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /settings/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Pull to refresh
// ---------------------------------------------------------------------------
test.describe('pull to refresh', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })

  // Constructs real Touch/TouchEvent objects inside the page (not via
  // page.touchscreen, which only supports taps) so the component's own
  // touchstart/touchmove/touchend listeners see a real pull gesture.
  async function pullDown(page, selector, pixels) {
    await page.evaluate(({ selector, pixels }) => {
      const el = document.querySelector(selector)
      const startY = 50
      const touch = (clientY) => new Touch({ identifier: 1, target: el, clientX: 40, clientY })
      el.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(startY)], bubbles: true, cancelable: true }))
      const steps = 5
      for (let i = 1; i <= steps; i++) {
        const y = startY + (pixels * i) / steps
        el.dispatchEvent(new TouchEvent('touchmove', { touches: [touch(y)], bubbles: true, cancelable: true }))
      }
      el.dispatchEvent(new TouchEvent('touchend', { touches: [], bubbles: true, cancelable: true }))
    }, { selector, pixels })
  }

  test('pulling past the threshold at scroll-top triggers a refresh', async ({ page }) => {
    let cardFetches = 0
    await page.route('**/api/cards', r => { cardFetches++; return r.fulfill({ json: ALL_TODOS }) })
    await page.goto('/today')
    await waitForApp(page)
    expect(cardFetches).toBe(1)

    await pullDown(page, '.board-wrapper', 200) // well past the 70px threshold

    await expect.poll(() => cardFetches).toBeGreaterThan(1)
  })

  test('pulling below the threshold does not trigger a refresh', async ({ page }) => {
    let cardFetches = 0
    await page.route('**/api/cards', r => { cardFetches++; return r.fulfill({ json: ALL_TODOS }) })
    await page.goto('/today')
    await waitForApp(page)
    expect(cardFetches).toBe(1)

    await pullDown(page, '.board-wrapper', 40) // well under the 70px threshold

    await page.waitForTimeout(300)
    expect(cardFetches).toBe(1)
  })

  test('starting a pull when not scrolled to the top does not trigger the gesture', async ({ page }) => {
    let cardFetches = 0
    await page.route('**/api/cards', r => { cardFetches++; return r.fulfill({ json: ALL_TODOS }) })
    await page.goto('/today')
    await waitForApp(page)
    expect(cardFetches).toBe(1)

    // Force real scrollability regardless of how much mock content rendered,
    // then scroll down so the gesture starts away from scrollTop === 0.
    await page.evaluate(() => {
      const el = document.querySelector('.board-wrapper')
      const spacer = document.createElement('div')
      spacer.style.height = '2000px'
      el.appendChild(spacer)
      el.scrollTop = 500
    })

    await pullDown(page, '.board-wrapper', 200)

    await page.waitForTimeout(300)
    expect(cardFetches).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// Settings modals
// ---------------------------------------------------------------------------
test.describe('settings modals', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/settings/export-token', r =>
      r.fulfill({ json: 'test-export-token' }))
    await page.goto('/today')
    await waitForApp(page)
  })

  test('settings menu groups general settings before per-page settings, in nav order', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    const labels = await page.getByRole('menuitem').allTextContents()
    const generalOrder = ['Navigation', 'Tags', 'Units', 'Notifications', 'Keyboard shortcuts', 'Telegram']
    const perPageOrder = ['Show briefing automatically', 'Calendar', 'Withings', 'Engineering (GitHub)']
    const indexOf = (needle) => labels.findIndex((l) => l.includes(needle))

    const generalIndices = generalOrder.map(indexOf)
    const perPageIndices = perPageOrder.map(indexOf)
    expect(generalIndices.every((i) => i >= 0)).toBe(true)
    expect(perPageIndices.every((i) => i >= 0)).toBe(true)
    // Each group internally ordered as expected...
    expect([...generalIndices]).toEqual([...generalIndices].sort((a, b) => a - b))
    expect([...perPageIndices]).toEqual([...perPageIndices].sort((a, b) => a - b))
    // ...and every general item precedes every per-page item.
    expect(Math.max(...generalIndices)).toBeLessThan(Math.min(...perPageIndices))
    // Sign out comes last, after both groups.
    expect(indexOf('Sign out')).toBeGreaterThan(Math.max(...perPageIndices))
  })

  test('Export data menu item triggers a request to the export endpoint', async ({ page }) => {
    await page.route('**/api/export', r => r.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'content-disposition': 'attachment; filename="quantum-task-export-20260603.json"' },
      body: JSON.stringify({ cards: [] }),
    }))
    await page.getByRole('button', { name: /settings/i }).click()
    await Promise.all([
      page.waitForRequest('**/api/export'),
      page.getByRole('menuitem', { name: /export data/i }).click(),
    ])
  })

  test('tag manager opens with Manage Tags heading and Close footer button', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    await expect(page.getByRole('heading', { name: 'Manage Tags' })).toBeVisible()
    await expect(page.getByRole('button', { name: /close/i })).toBeVisible()
    await expect(page.locator('.modal-close-btn')).toHaveCount(0)
  })

  test('tag manager lists existing tags', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    await expect(modal.locator('.tag-mgr-name', { hasText: 'work' })).toBeVisible()
    await expect(modal.locator('.tag-mgr-name', { hasText: 'personal' })).toBeVisible()
  })

  test('report icon button is visible on each tag row', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    const row = modal.locator('.tag-mgr-row', { hasText: 'work' })
    await expect(row.locator('.tag-mgr-report')).toBeVisible()
  })

  test('clicking the report icon opens the report modal for that tag', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    await modal.locator('.tag-mgr-row', { hasText: 'work' }).locator('.tag-mgr-report').click()
    await expect(page.getByRole('heading', { name: 'Report: work' })).toBeVisible()
    await expect(page.getByRole('button', { name: /^done$/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /^to do$/i })).toBeVisible()
  })

  test('generating a report shows the returned markdown and item count', async ({ page }) => {
    // Registered after the broader mock below so it wins for this more specific URL
    // (Playwright tries routes most-recently-registered first).
    await page.route('**/api/reports/tag/period-counts*', r => r.fulfill({ json: { counts: {
      today: 1, this_week: 1, last_week: 1, this_month: 1, last_month: 1, last_7_days: 1, last_30_days: 1,
    }}}))
    await page.route('**/api/reports/tag*', r => r.fulfill({ json: {
      tag_name: 'work', mode: 'done', start: '2026-06-01', end: '2026-06-03',
      items: [{ id: 1, title: 'Ship the fix', date: '2026-06-02' }], count: 1,
      markdown: '### Done: work (2026-06-01 to 2026-06-03)\n\n- Ship the fix (2026-06-02)',
    }}))
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    await modal.locator('.tag-mgr-row', { hasText: 'work' }).locator('.tag-mgr-report').click()
    await page.getByRole('button', { name: /^generate$/i }).click()
    await expect(page.getByText('1 item', { exact: true })).toBeVisible()
    await expect(page.getByText('Ship the fix (2026-06-02)')).toBeVisible()
    await expect(page.getByRole('button', { name: /copy/i })).toBeVisible()
  })

  test('an empty report shows the "nothing found" message from the server', async ({ page }) => {
    await page.route('**/api/reports/tag/period-counts*', r => r.fulfill({ json: { counts: {
      today: 1, this_week: 1, last_week: 1, this_month: 1, last_month: 1, last_7_days: 1, last_30_days: 1,
    }}}))
    await page.route('**/api/reports/tag*', r => r.fulfill({ json: {
      tag_name: 'work', mode: 'todo', start: '2026-06-01', end: '2026-06-03',
      items: [], count: 0,
      markdown: '### To do: work (2026-06-01 to 2026-06-03)\n\n_Nothing found for this tag and period._',
    }}))
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    await modal.locator('.tag-mgr-row', { hasText: 'work' }).locator('.tag-mgr-report').click()
    await page.getByRole('button', { name: /^to do$/i }).click()
    await page.getByRole('button', { name: /^generate$/i }).click()
    await expect(page.getByText('Nothing found for this tag and period.')).toBeVisible()
  })

  test('switching to custom range shows date inputs instead of period pills', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    await modal.locator('.tag-mgr-row', { hasText: 'work' }).locator('.tag-mgr-report').click()
    await page.getByRole('button', { name: /custom range/i }).click()
    await expect(page.getByLabel('Start date')).toBeVisible()
    await expect(page.getByLabel('End date')).toBeVisible()
    await expect(page.getByRole('button', { name: /this week/i })).toHaveCount(0)
  })

  test('a period with zero items is disabled and shows a count of 0', async ({ page }) => {
    await page.route('**/api/reports/tag/period-counts*', r => r.fulfill({ json: { counts: {
      today: 0, this_week: 3, last_week: 0, this_month: 5, last_month: 0, last_7_days: 3, last_30_days: 5,
    }}}))
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    await modal.locator('.tag-mgr-row', { hasText: 'work' }).locator('.tag-mgr-report').click()
    const lastWeek = page.getByRole('button', { name: /^last week/i })
    await expect(lastWeek).toBeDisabled()
    await expect(lastWeek).toContainText('0')
    const thisWeek = page.getByRole('button', { name: /^this week/i })
    await expect(thisWeek).toBeEnabled()
    await expect(thisWeek).toContainText('3')
  })

  test('an empty-count selected period auto-switches to the first period with items', async ({ page }) => {
    await page.route('**/api/reports/tag/period-counts*', r => r.fulfill({ json: { counts: {
      today: 0, this_week: 0, last_week: 4, this_month: 4, last_month: 0, last_7_days: 0, last_30_days: 4,
    }}}))
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    // Default selection is "this_week", which this mock reports as empty.
    await modal.locator('.tag-mgr-row', { hasText: 'work' }).locator('.tag-mgr-report').click()
    await expect(page.getByRole('button', { name: /^last week/i })).toHaveClass(/--active/)
  })

  test('the Generate button is disabled while the selected period has zero items', async ({ page }) => {
    await page.route('**/api/reports/tag/period-counts*', r => r.fulfill({ json: { counts: {
      today: 0, this_week: 0, last_week: 0, this_month: 0, last_month: 0, last_7_days: 0, last_30_days: 0,
    }}}))
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    await modal.locator('.tag-mgr-row', { hasText: 'work' }).locator('.tag-mgr-report').click()
    await expect(page.getByRole('button', { name: /^generate$/i })).toBeDisabled()
  })

  test('a custom range stays generatable even when quick periods have no items', async ({ page }) => {
    await page.route('**/api/reports/tag/period-counts*', r => r.fulfill({ json: { counts: {
      today: 0, this_week: 0, last_week: 0, this_month: 0, last_month: 0, last_7_days: 0, last_30_days: 0,
    }}}))
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /tags/i }).click()
    const modal = page.getByRole('dialog')
    await modal.locator('.tag-mgr-row', { hasText: 'work' }).locator('.tag-mgr-report').click()
    await page.getByRole('button', { name: /custom range/i }).click()
    await page.getByLabel('Start date').fill('2026-01-01')
    await page.getByLabel('End date').fill('2026-01-31')
    await expect(page.getByRole('button', { name: /^generate$/i })).toBeEnabled()
  })

  test('calendar settings opens with heading and Save/Cancel footer buttons', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /calendar/i }).click()
    await expect(page.getByRole('heading', { name: 'Calendar Settings' })).toBeVisible()
    await expect(page.getByRole('button', { name: /save/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /cancel/i })).toBeVisible()
    await expect(page.locator('.modal-close-btn')).toHaveCount(0)
  })

  test('calendar settings shows export URL section', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /calendar/i }).click()
    await expect(page.getByText(/export tasks as ical/i)).toBeVisible()
    await expect(page.getByRole('button', { name: /copy/i })).toBeVisible()
  })

  test('withings settings opens with heading and Connect button when not connected', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /withings/i }).click()
    await expect(page.getByRole('heading', { name: 'Withings' })).toBeVisible()
    await expect(page.getByText('Not connected', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: /connect withings/i })).toBeVisible()
  })

  test('navigation settings opens with heading, ordered pages, and Save/Cancel footer buttons', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /navigation/i }).click()
    await expect(page.getByRole('heading', { name: 'Navigation' })).toBeVisible()
    const rows = page.locator('.nav-settings-row')
    await expect(rows).toHaveCount(5)
    await expect(rows.nth(0)).toContainText('Today')
    await expect(rows.nth(1)).toContainText('Board')
    await expect(page.getByRole('button', { name: /save/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /cancel/i })).toBeVisible()
  })

  test('navigation settings reorders pages with move buttons', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /navigation/i }).click()
    const rows = page.locator('.nav-settings-row')
    await rows.nth(1).getByRole('button', { name: /move board up/i }).click()
    await expect(rows.nth(0)).toContainText('Board')
    await expect(rows.nth(1)).toContainText('Today')
  })

  test('navigation settings saves reordered pages and chosen default page', async ({ page }) => {
    let putBody = null
    await page.route('**/api/settings/navigation', r => {
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: putBody })
      }
      return r.fulfill({ json: { order: ['today', 'board', 'calendar', 'health', 'engineering'], default_page: 'today' } })
    })
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /navigation/i }).click()
    await page.locator('.nav-settings-row', { hasText: 'Board' }).getByRole('button', { name: /move board up/i }).click()
    await page.getByLabel(/default page/i).selectOption('board')
    await page.getByRole('button', { name: /^save$/i }).click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody.order[0]).toBe('board')
    expect(putBody.default_page).toBe('board')
    await expect(page.getByRole('heading', { name: 'Navigation' })).not.toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Engineering page
// ---------------------------------------------------------------------------
test.describe('engineering page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/engineering')
    await waitForApp(page)
  })

  test('empty state is shown when no items', async ({ page }) => {
    await expect(page.locator('.eng-empty')).toBeVisible()
  })

  test('sync button is visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: /sync/i })).toBeVisible()
  })

  test('shows PR and issue sections when items are present', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/pull/1', title: 'Fix login bug', item_type: 'pr',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/pull/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString() },
      { id: 2, external_id: 'github:org/repo/issues/2', title: 'Add dark mode', item_type: 'issue',
        repo: 'org/repo', number: 2, url: 'https://github.com/org/repo/issues/2', state: 'open',
        project_name: 'My Board', project_status: 'In Progress', synced_at: new Date().toISOString() },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    await expect(page.getByText('PRs to Review')).toBeVisible()
    await expect(page.getByText('Assigned Issues')).toBeVisible()
    await expect(page.getByText('Fix login bug')).toBeVisible()
    await expect(page.getByText('Add dark mode')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add to board' }).first()).toBeVisible()
  })

  test('items show repo tag pills when the item has tags', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/pull/1', title: 'Fix login bug', item_type: 'pr',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/pull/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(),
        tags: [{ id: 1, name: 'MyApp', color: '#3b82f6', is_project: false }] },
      { id: 2, external_id: 'github:org/other/issues/2', title: 'Add dark mode', item_type: 'issue',
        repo: 'org/other', number: 2, url: 'https://github.com/org/other/issues/2', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(),
        tags: [] },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    const taggedItem = page.locator('.eng-item', { hasText: 'Fix login bug' })
    await expect(taggedItem.locator('.eng-item-tag', { hasText: 'MyApp' })).toBeVisible()

    const untaggedItem = page.locator('.eng-item', { hasText: 'Add dark mode' })
    await expect(untaggedItem.locator('.eng-item-tag')).toHaveCount(0)
  })

  test('more than 2 tags shows a "+N" overflow chip instead of widening the row', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/pull/1', title: 'Fix login bug', item_type: 'pr',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/pull/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(),
        tags: [
          { id: 1, name: 'MyApp', color: '#3b82f6', is_project: false },
          { id: 2, name: 'Urgent', color: '#ef4444', is_project: false },
          { id: 3, name: 'Backend', color: '#10b981', is_project: false },
        ] },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    const item = page.locator('.eng-item', { hasText: 'Fix login bug' })
    await expect(item.locator('.eng-item-tag', { hasText: 'MyApp' })).toBeVisible()
    await expect(item.locator('.eng-item-tag', { hasText: 'Urgent' })).toBeVisible()
    await expect(item.locator('.eng-item-tag', { hasText: 'Backend' })).toHaveCount(0)
    await expect(item.locator('.eng-item-tag--overflow', { hasText: '+1' })).toBeVisible()
  })

  test('sidebar tag filter narrows the engineering item list', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/pull/1', title: 'Fix login bug', item_type: 'pr',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/pull/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(),
        tags: [{ id: 1, name: 'work', color: '#3b82f6' }] },
      { id: 2, external_id: 'github:org/other/issues/2', title: 'Add dark mode', item_type: 'issue',
        repo: 'org/other', number: 2, url: 'https://github.com/org/other/issues/2', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(),
        tags: [] },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    await page.locator('.sidebar-item', { hasText: 'work' }).click()
    await expect(page).toHaveURL(/\/engineering\/tag\/1/)
    await expect(page.getByText('Fix login bug')).toBeVisible()
    await expect(page.getByText('Add dark mode')).toHaveCount(0)
  })

  test('switching to engineering from another page preserves the tag selection', async ({ page }) => {
    await page.goto('/board/tag/1')
    await waitForApp(page)
    await page.locator('.sidebar-item', { hasText: 'Engineering' }).click()
    await expect(page).toHaveURL(/\/engineering\/tag\/1/)
  })

  test('status pill and action button are on the same line as the title', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/issues/1', title: 'Fix login bug', item_type: 'issue',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/issues/1', state: 'open',
        project_name: 'Board', project_status: 'Backlog', synced_at: new Date().toISOString(), tags: [] },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    const item = page.locator('.eng-item', { hasText: 'Fix login bug' })
    const titleBox = await item.locator('.eng-item-title').boundingBox()
    const statusBox = await item.locator('.eng-item-status-pill').boundingBox()
    const actionBox = await item.locator('.eng-add-btn').boundingBox()
    // "Same line" — vertical centers within a few px of each other
    expect(Math.abs(titleBox.y + titleBox.height / 2 - (statusBox.y + statusBox.height / 2))).toBeLessThan(4)
    expect(Math.abs(titleBox.y + titleBox.height / 2 - (actionBox.y + actionBox.height / 2))).toBeLessThan(4)
  })

  test('a PR with no activity in over a week shows a Stale badge', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/pull/1', title: 'Old PR', item_type: 'pr',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/pull/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(),
        body_updated_at: '2026-05-24T10:00:00Z', tags: [] },
      { id: 2, external_id: 'github:org/repo/pull/2', title: 'Fresh PR', item_type: 'pr',
        repo: 'org/repo', number: 2, url: 'https://github.com/org/repo/pull/2', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(),
        body_updated_at: '2026-06-02T10:00:00Z', tags: [] },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    const stale = page.locator('.eng-item', { hasText: 'Old PR' })
    await expect(stale.locator('.eng-item-stale-badge')).toBeVisible()
    await expect(stale.locator('.eng-item-stale-badge')).toHaveText('Stale 10d')

    const fresh = page.locator('.eng-item', { hasText: 'Fresh PR' })
    await expect(fresh.locator('.eng-item-stale-badge')).toHaveCount(0)
  })

  test('repo filter is hidden with a single repo, shown and functional with multiple', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/issues/1', title: 'Single repo item', item_type: 'issue',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/issues/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(), tags: [] },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    await expect(page.locator('.eng-repo-filter')).toHaveCount(0)

    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/issues/1', title: 'From repo one', item_type: 'issue',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/issues/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(), tags: [] },
      { id: 2, external_id: 'github:org/other/issues/2', title: 'From repo two', item_type: 'issue',
        repo: 'org/other', number: 2, url: 'https://github.com/org/other/issues/2', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(), tags: [] },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    await expect(page.locator('.eng-repo-filter')).toBeVisible()
    await expect(page.getByText('From repo one')).toBeVisible()
    await expect(page.getByText('From repo two')).toBeVisible()

    await page.locator('.eng-repo-pill', { hasText: 'repo' }).click()
    await expect(page.getByText('From repo one')).toBeVisible()
    await expect(page.getByText('From repo two')).toHaveCount(0)

    await page.locator('.eng-repo-pill', { hasText: 'All' }).click()
    await expect(page.getByText('From repo one')).toBeVisible()
    await expect(page.getByText('From repo two')).toBeVisible()
  })

  test('clicking the checkmark for an already-added item opens the board and that card', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/issues/1', title: 'Fix login bug', item_type: 'issue',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/issues/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(), tags: [] },
    ]}))
    await page.route('**/api/cards', r => r.fulfill({ json: [
      ...ALL_TODOS,
      {
        id: 999, title: 'Fix login bug', section: 'today', completed: false,
        description: '', position: 0, overdue_days: 0, tags: [],
        external_id: 'github:org/repo/issues/1',
      },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)

    await page.locator('.eng-item', { hasText: 'Fix login bug' })
      .getByRole('button', { name: /open card on board/i })
      .click()

    await expect(page).toHaveURL(/\/board/)
    const panel = page.locator('.card-detail-panel')
    await expect(panel).toBeVisible()
    // Default view mode, not edit — the panel shows an Edit button rather
    // than jumping straight into the edit form.
    await expect(panel.getByRole('button', { name: /^edit$/i })).toBeVisible()
    await expect(panel.getByText('Fix login bug')).toBeVisible()
  })

  test('mobile: clicking the checkmark opens the card sheet in view mode', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/issues/1', title: 'Fix login bug', item_type: 'issue',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/issues/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(), tags: [] },
    ]}))
    await page.route('**/api/cards', r => r.fulfill({ json: [
      ...ALL_TODOS,
      {
        id: 999, title: 'Fix login bug', section: 'today', completed: false,
        description: '', position: 0, overdue_days: 0, tags: [],
        external_id: 'github:org/repo/issues/1',
      },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)

    await page.locator('.eng-item', { hasText: 'Fix login bug' })
      .getByRole('button', { name: /open card on board/i })
      .click()

    await expect(page).toHaveURL(/\/board/)
    const sheet = page.locator('.card-sheet')
    await expect(sheet).toBeVisible()
    await expect(sheet.locator('.card-sheet-title', { hasText: 'Fix login bug' })).toBeVisible()
    await expect(sheet.getByRole('button', { name: /^edit$/i })).toBeVisible()
  })

  test('tags and repo/issue link sit on a line below the title', async ({ page }) => {
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/issues/1', title: 'Fix login bug', item_type: 'issue',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/issues/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(),
        tags: [{ id: 1, name: 'MyApp', color: '#3b82f6', is_project: false }] },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    const item = page.locator('.eng-item', { hasText: 'Fix login bug' })
    const titleBox = await item.locator('.eng-item-title').boundingBox()
    const subBox = await item.locator('.eng-item-sub').boundingBox()
    expect(subBox.y).toBeGreaterThan(titleBox.y + titleBox.height / 2)
  })

  test('clicking "+ Board" for an issue creates a card in This Week and the button becomes a checkmark', async ({ page }) => {
    let postedCard = null
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/issues/1', title: 'Fix login bug', item_type: 'issue',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/issues/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(), tags: [] },
    ]}))
    await page.route('**/api/cards', r => {
      if (r.request().method() === 'POST') {
        postedCard = r.request().postDataJSON()
        return r.fulfill({ status: 201, json: {
          id: 500, title: postedCard.title, section: postedCard.section, completed: false,
          description: '', position: 0, overdue_days: 0, tags: [], external_id: postedCard.external_id,
        }})
      }
      return r.fulfill({ json: ALL_TODOS })
    })
    await page.goto('/engineering')
    await waitForApp(page)

    const item = page.locator('.eng-item', { hasText: 'Fix login bug' })
    await item.getByRole('button', { name: /add to board/i }).click()

    await expect.poll(() => postedCard).not.toBeNull()
    expect(postedCard.title).toBe('Fix login bug')
    expect(postedCard.section).toBe('week')
    expect(postedCard.external_id).toBe('github:org/repo/issues/1')

    // Reactively switches to the checkmark without a page reload
    await expect(item.getByRole('button', { name: /open card on board/i })).toBeVisible()
  })

  test('clicking "+ Board" for a PR creates a card in Today', async ({ page }) => {
    let postedCard = null
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 2, external_id: 'github:org/repo/pull/2', title: 'Bump deps', item_type: 'pr',
        repo: 'org/repo', number: 2, url: 'https://github.com/org/repo/pull/2', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString(), tags: [] },
    ]}))
    await page.route('**/api/cards', r => {
      if (r.request().method() === 'POST') {
        postedCard = r.request().postDataJSON()
        return r.fulfill({ status: 201, json: {
          id: 501, title: postedCard.title, section: postedCard.section, completed: false,
          description: '', position: 0, overdue_days: 0, tags: [], external_id: postedCard.external_id,
        }})
      }
      return r.fulfill({ json: ALL_TODOS })
    })
    await page.goto('/engineering')
    await waitForApp(page)

    await page.locator('.eng-item', { hasText: 'Bump deps' })
      .getByRole('button', { name: /add to board/i })
      .click()

    await expect.poll(() => postedCard).not.toBeNull()
    expect(postedCard.title).toBe('Bump deps')
    expect(postedCard.section).toBe('today')
  })

  test('no Builds section is shown when there are no bridge jobs', async ({ page }) => {
    await expect(page.getByText('Builds')).not.toBeVisible()
  })

  test('Builds section appears above PRs to Review when jobs exist', async ({ page }) => {
    await page.route('**/api/bridge/jobs/dashboard', r => r.fulfill({ json: { jobs: [
      { id: 1, card_id: 1, card_title: 'Daily Engineering Standup', status: 'running',
        target_repo: null, branch_name: 'qtask/1-standup', agent_name: 'work-mac', result: null,
        depends_on_job_id: null, resumes_job_id: null,
        created_at: '2026-06-03T09:00:00Z', updated_at: '2026-06-03T09:05:00Z' },
    ] } }))
    await page.route('**/api/engineering/items', r => r.fulfill({ json: [
      { id: 1, external_id: 'github:org/repo/pull/1', title: 'Fix login bug', item_type: 'pr',
        repo: 'org/repo', number: 1, url: 'https://github.com/org/repo/pull/1', state: 'open',
        project_name: null, project_status: null, synced_at: new Date().toISOString() },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)

    await expect(page.getByText('Builds')).toBeVisible()
    await expect(page.locator('.eng-build-row', { hasText: 'Daily Engineering Standup' })).toBeVisible()
    await expect(page.locator('.eng-build-row', { hasText: 'Running' })).toBeVisible()
    await expect(page.locator('.eng-build-row', { hasText: 'qtask/1-standup' })).toBeVisible()

    const sectionOrder = await page.locator('.eng-section-heading').allTextContents()
    const buildsIndex = sectionOrder.findIndex((t) => t.includes('Builds'))
    const prsIndex = sectionOrder.findIndex((t) => t.includes('PRs to Review'))
    expect(buildsIndex).toBeGreaterThanOrEqual(0)
    expect(buildsIndex).toBeLessThan(prsIndex)
  })

  test('clicking a build row for a known card opens it', async ({ page }) => {
    await page.route('**/api/bridge/jobs/dashboard', r => r.fulfill({ json: { jobs: [
      { id: 1, card_id: 1, card_title: 'Daily Engineering Standup', status: 'error',
        target_repo: null, branch_name: 'qtask/1-standup', agent_name: 'work-mac', result: 'boom',
        depends_on_job_id: null, resumes_job_id: null,
        created_at: '2026-06-03T09:00:00Z', updated_at: '2026-06-03T09:05:00Z' },
    ] } }))
    await page.goto('/engineering')
    await waitForApp(page)

    await page.locator('.eng-build-row', { hasText: 'Daily Engineering Standup' }).click()
    await expect(page.locator('.cdp-title', { hasText: 'Daily Engineering Standup' })).toBeVisible()
  })

  test('a build row for an unknown card is shown but not clickable', async ({ page }) => {
    await page.route('**/api/bridge/jobs/dashboard', r => r.fulfill({ json: { jobs: [
      { id: 1, card_id: 999999, card_title: '(deleted card)', status: 'done',
        target_repo: null, branch_name: 'qtask/999999-old', agent_name: 'work-mac', result: 'done',
        depends_on_job_id: null, resumes_job_id: null,
        created_at: '2026-06-03T09:00:00Z', updated_at: '2026-06-03T09:05:00Z' },
    ] } }))
    await page.goto('/engineering')
    await waitForApp(page)

    const row = page.locator('.eng-build-row', { hasText: '(deleted card)' })
    await expect(row).toBeVisible()
    await expect(row).not.toHaveClass(/eng-build-row--clickable/)
  })

  test('a companion job shows its target repo', async ({ page }) => {
    await page.route('**/api/bridge/jobs/dashboard', r => r.fulfill({ json: { jobs: [
      { id: 2, card_id: 1, card_title: 'Daily Engineering Standup', status: 'blocked',
        target_repo: 'owner/web-repo', branch_name: null, agent_name: null, result: null,
        depends_on_job_id: 1, resumes_job_id: null,
        created_at: '2026-06-03T09:00:00Z', updated_at: null },
    ] } }))
    await page.goto('/engineering')
    await waitForApp(page)

    await expect(page.locator('.eng-build-row', { hasText: 'owner/web-repo' })).toBeVisible()
    await expect(page.locator('.eng-build-row', { hasText: 'Blocked' })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Sidebar upcoming events
// ---------------------------------------------------------------------------
test.describe('sidebar tags', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('tags section is visible in sidebar', async ({ page }) => {
    await expect(page.locator('.sidebar-section-label', { hasText: 'Tags' })).toBeVisible()
  })

  test('tag filter buttons are visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'work', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'personal', exact: true })).toBeVisible()
  })

  test('a report icon is present next to each sidebar tag', async ({ page }) => {
    const row = page.locator('.sidebar-tag-row', { hasText: 'work' })
    await expect(row.locator('.sidebar-tag-report')).toBeVisible()
  })

  test('clicking the sidebar report icon opens the report modal for that tag', async ({ page }) => {
    await page.locator('.sidebar-tag-row', { hasText: 'work' }).locator('.sidebar-tag-report').click()
    await expect(page.getByRole('heading', { name: 'Report: work' })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------
test.describe('keyboard shortcuts', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('n opens quick add modal', async ({ page }) => {
    await page.keyboard.press('n')
    await expect(page.getByRole('dialog')).toBeVisible()
  })

  test('/ opens search modal', async ({ page }) => {
    await page.keyboard.press('/')
    await expect(page.getByRole('dialog')).toBeVisible()
  })

  test('t navigates to today page', async ({ page }) => {
    await page.keyboard.press('t')
    await expect(page).toHaveURL(/\/today/)
  })

  test('b navigates to board page', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await page.keyboard.press('b')
    await expect(page).toHaveURL(/\/board/)
  })

  test('h navigates to health page', async ({ page }) => {
    await page.keyboard.press('h')
    await expect(page).toHaveURL(/\/health/)
  })

  test('c navigates to calendar page', async ({ page }) => {
    await page.keyboard.press('c')
    await expect(page).toHaveURL(/\/calendar/)
  })

  test('e navigates to engineering page', async ({ page }) => {
    await page.keyboard.press('e')
    await expect(page).toHaveURL(/\/engineering/)
  })

  test('? opens keyboard shortcuts modal', async ({ page }) => {
    await page.keyboard.press('?')
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Keyboard Shortcuts' })).toBeVisible()
    await expect(page.getByRole('dialog').locator('kbd').first()).toBeVisible()
  })

  test('shortcuts do not fire when a modal is open', async ({ page }) => {
    await page.keyboard.press('n')
    await expect(page.getByRole('dialog')).toBeVisible()
    // quick-add textarea is autofocused; pressing n again should type, not open another modal
    await page.keyboard.press('n')
    await expect(page.getByRole('dialog')).toHaveCount(1)
  })
})

// ---------------------------------------------------------------------------
// Offline banner
// ---------------------------------------------------------------------------
test.describe('offline banner', () => {
  test('banner appears when offline event fires', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)

    // Banner not shown while online
    await expect(page.locator('.offline-banner')).toHaveCount(0)

    // Simulate going offline
    await page.evaluate(() => window.dispatchEvent(new Event('offline')))
    await expect(page.locator('.offline-banner')).toBeVisible()
  })

  test('banner disappears when connection is restored', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)

    await page.evaluate(() => window.dispatchEvent(new Event('offline')))
    await expect(page.locator('.offline-banner')).toBeVisible()

    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    await expect(page.locator('.offline-banner')).toHaveCount(0)
  })

  test('header remains visible while offline', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)

    await page.evaluate(() => window.dispatchEvent(new Event('offline')))
    await expect(page.locator('.offline-banner')).toBeVisible()
    await expect(page.locator('.app-header')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Assistant modal
// ---------------------------------------------------------------------------
const ASSIST_LIST_SSE =
  'data: {"text":"1. Go to the dentist\\n"}\n\n' +
  'data: {"text":"2. Get a cleaning\\n"}\n\n' +
  'data: {"text":"3. Schedule follow-up\\n"}\n\n' +
  'data: [DONE]\n\n'

test.describe('assistant modal', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('"✦ Assist" button is visible in the card detail panel', async ({ page }) => {
    const card = page.locator('.event-card', { hasText: 'Daily Engineering Standup' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await expect(panel.getByRole('button', { name: /assist/i })).toBeVisible()
  })

  test('clicking Assist opens inline assistant with Chat and Break down tabs', async ({ page }) => {
    const card = page.locator('.event-card', { hasText: 'Daily Engineering Standup' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await expect(page.locator('.assist-inline')).toBeVisible()
    await expect(page.locator('.assist-tabs')).toBeVisible()
  })

  test('chat input and send button are visible in the inline assistant', async ({ page }) => {
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await expect(page.locator('.assist-inline')).toBeVisible()
    await expect(page.locator('.assist-input')).toBeVisible()
    await expect(page.locator('.assist-send')).toBeVisible()
  })

  test('"Break down" tab shows editable subtask list', async ({ page }) => {
    await page.route('**/api/cards/3/breakdown', (r) =>
      r.fulfill({ json: { subtasks: ['Go to the dentist', 'Get a cleaning', 'Schedule follow-up'], tag_name: 'Project: Call dentist' } })
    )
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await page.getByRole('button', { name: /^break down$/i }).click()
    await expect(page.locator('.assist-bd-input').nth(0)).toHaveValue('Go to the dentist')
    await expect(page.locator('.assist-bd-input').nth(1)).toHaveValue('Get a cleaning')
    await expect(page.locator('.assist-bd-input').nth(2)).toHaveValue('Schedule follow-up')
    await expect(page.getByRole('button', { name: /create 3 subtasks/i })).toBeVisible()
  })

  test('switching back to Chat tab from Break down shows chat input', async ({ page }) => {
    await page.route('**/api/cards/3/breakdown', (r) =>
      r.fulfill({ json: { subtasks: ['Step 1'], tag_name: 'Project: Call dentist' } })
    )
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await page.getByRole('button', { name: /^break down$/i }).click()
    await expect(page.locator('.assist-bd-input').first()).toBeVisible()
    await page.getByRole('button', { name: /^chat$/i }).click()
    await expect(page.locator('.assist-bd-input')).toHaveCount(0)
    await expect(page.locator('.assist-input')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Card action-item extraction
// ---------------------------------------------------------------------------
test.describe('card action-item extraction', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('"Extract action items" button is visible on a card with a description', async ({ page }) => {
    await page.locator('.event-card', { hasText: 'Shopping list' }).click()
    const panel = page.locator('.card-detail-panel')
    await expect(panel.getByRole('button', { name: /extract action items/i })).toBeVisible()
  })

  test('button is not shown on a card with no description', async ({ page }) => {
    await page.locator('.event-card', { hasText: 'Call dentist' }).click()
    const panel = page.locator('.card-detail-panel')
    await expect(panel.getByRole('button', { name: /extract action items/i })).toHaveCount(0)
  })

  test('clicking the button opens the bulk-confirm review screen pre-populated with extracted items', async ({ page }) => {
    await page.route('**/api/cards/7/extract-actions', r => r.fulfill({ json: { items: [
      { type: 'task', title: 'Buy coffee', description: null, section: 'later', scheduled_at: null, suggested_tags: [] },
      { type: 'task', title: 'Buy milk', description: null, section: 'later', scheduled_at: null, suggested_tags: [] },
    ]}}))
    await page.locator('.event-card', { hasText: 'Shopping list' }).click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /extract action items/i }).click()
    await expect(page.getByText('Add 2 Items')).toBeVisible()
    await expect(page.getByText('Buy coffee')).toBeVisible()
    await expect(page.getByText('Buy milk')).toBeVisible()
  })

  test('an extracted item can be removed before confirming', async ({ page }) => {
    await page.route('**/api/cards/7/extract-actions', r => r.fulfill({ json: { items: [
      { type: 'task', title: 'Buy coffee', description: null, section: 'later', scheduled_at: null, suggested_tags: [] },
      { type: 'task', title: 'Buy milk', description: null, section: 'later', scheduled_at: null, suggested_tags: [] },
    ]}}))
    await page.locator('.event-card', { hasText: 'Shopping list' }).click()
    await page.locator('.card-detail-panel').getByRole('button', { name: /extract action items/i }).click()
    await expect(page.getByText('Add 2 Items')).toBeVisible()
    await page.locator('.quick-bulk-item', { hasText: 'Buy milk' }).getByRole('button', { name: /remove/i }).click()
    await expect(page.getByText('Add 1 Item', { exact: true })).toBeVisible()
    await expect(page.getByText('Buy milk')).toHaveCount(0)
  })

  test('no action items found shows an empty state instead of a blank list', async ({ page }) => {
    await page.route('**/api/cards/7/extract-actions', r => r.fulfill({ json: { items: [] } }))
    await page.locator('.event-card', { hasText: 'Shopping list' }).click()
    await page.locator('.card-detail-panel').getByRole('button', { name: /extract action items/i }).click()
    await expect(page.getByText('No items found.')).toBeVisible()
    await expect(page.getByRole('button', { name: /^add all$/i })).toBeDisabled()
  })

  test('a failed extraction shows an inline error and re-enables the button', async ({ page }) => {
    await page.route('**/api/cards/7/extract-actions', r => r.fulfill({ status: 503, json: {} }))
    await page.locator('.event-card', { hasText: 'Shopping list' }).click()
    const panel = page.locator('.card-detail-panel')
    const btn = panel.getByRole('button', { name: /extract action items/i })
    await btn.click()
    await expect(panel.getByText(/failed to extract action items/i)).toBeVisible()
    await expect(btn).toBeEnabled()
  })

  test('closing quick add after an extraction resets it to the input step next time', async ({ page }) => {
    await page.route('**/api/cards/7/extract-actions', r => r.fulfill({ json: { items: [
      { type: 'task', title: 'Buy coffee', description: null, section: 'later', scheduled_at: null, suggested_tags: [] },
    ]}}))
    await page.locator('.event-card', { hasText: 'Shopping list' }).click()
    await page.locator('.card-detail-panel').getByRole('button', { name: /extract action items/i }).click()
    await expect(page.getByText('Add 1 Item', { exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.quick-modal')).toHaveCount(0)

    await page.locator('button.btn-primary').first().click()
    await expect(page.locator('.quick-modal')).toBeVisible()
    await expect(page.getByRole('textbox')).toBeVisible()
    await expect(page.getByText('Add 1 Item', { exact: true })).toHaveCount(0)
  })

})

// ---------------------------------------------------------------------------
// Mobile card sheet
// ---------------------------------------------------------------------------
test.describe('mobile card sheet', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('tapping a card opens the bottom sheet instead of expanding inline', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    // force:true bypasses dnd-kit's aria-disabled="true" (means "not draggable", not "not clickable")
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    await expect(page.locator('.card-sheet')).toBeVisible()
    // Inline expansion must NOT have happened
    await expect(page.locator('.event-details')).toHaveCount(0)
  })

  test('sheet shows card title and view-mode action buttons', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    const sheet = page.locator('.card-sheet')
    await expect(sheet.locator('.card-sheet-title', { hasText: 'Call dentist' })).toBeVisible()
    await expect(sheet.getByRole('button', { name: /^complete$/i })).toBeVisible()
    await expect(sheet.getByRole('button', { name: /^edit$/i })).toBeVisible()
  })

  test('sheet shows description text for a card that has one', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    // Shopping list is in the Later section
    await page.locator('.mobile-tab', { hasText: 'Later' }).click()
    await page.locator('.event-card', { hasText: 'Shopping list' }).click({ force: true })
    const sheet = page.locator('.card-sheet')
    await expect(sheet).toBeVisible()
    await expect(sheet.getByText('Milk')).toBeVisible()
  })

  test('"Extract action items" opens the bulk-confirm screen from the sheet too', async ({ page }) => {
    await page.route('**/api/cards/7/extract-actions', r => r.fulfill({ json: { items: [
      { type: 'task', title: 'Buy coffee', description: null, section: 'later', scheduled_at: null, suggested_tags: [] },
    ]}}))
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.mobile-tab', { hasText: 'Later' }).click()
    await page.locator('.event-card', { hasText: 'Shopping list' }).click({ force: true })
    await page.locator('.card-sheet').getByRole('button', { name: /extract action items/i }).click()
    await expect(page.getByText('Add 1 Item', { exact: true })).toBeVisible()
    await expect(page.getByText('Buy coffee')).toBeVisible()
  })

  test('close button dismisses the sheet', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    await expect(page.locator('.card-sheet')).toBeVisible()
    await page.locator('.card-sheet').getByRole('button', { name: /close/i }).click()
    await expect(page.locator('.card-sheet')).toHaveCount(0)
  })

  test('Edit button switches sheet to edit form', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    await page.locator('.card-sheet').getByRole('button', { name: /^edit$/i }).click()
    const sheet = page.locator('.card-sheet')
    await expect(sheet.locator('.card-sheet-title', { hasText: 'Edit Card' })).toBeVisible()
    await expect(sheet.locator('#cs-title')).toBeVisible()
    await expect(sheet.locator('#cs-desc')).toBeVisible()
    await expect(sheet.getByRole('button', { name: /^save$/i })).toBeVisible()
  })

  test('description textarea is taller in the sheet edit form than in the standard modal', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    await page.locator('.card-sheet').getByRole('button', { name: /^edit$/i }).click()
    const textarea = page.locator('#cs-desc')
    await expect(textarea).toBeVisible()
    const box = await textarea.boundingBox()
    // rows=3 default is ~72px; our override sets min-height: 160px
    expect(box.height).toBeGreaterThan(120)
  })

  test('"Add card" button opens a new-card sheet instead of AddTodoModal', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.column-add-btn').first().click()
    const sheet = page.locator('.card-sheet')
    await expect(sheet).toBeVisible()
    await expect(sheet.locator('.card-sheet-title', { hasText: 'New Card' })).toBeVisible()
    await expect(sheet.locator('#cs-title')).toBeVisible()
    await expect(sheet.getByRole('button', { name: /add card/i })).toBeVisible()
    // The standard centered modal must NOT have opened
    await expect(page.locator('.modal')).toHaveCount(0)
  })

  test('creating a new tag inline while saving attaches it to the card', async ({ page }) => {
    let postedTagIds = null
    await page.route('**/api/tags', (r) => {
      if (r.request().method() === 'POST') {
        const body = r.request().postDataJSON()
        return r.fulfill({ json: { id: 99, name: body.name, color: body.color, is_project: false } })
      }
      return r.fulfill({ json: TAGS })
    })
    await page.route('**/api/cards', (r) => {
      if (r.request().method() === 'POST') {
        postedTagIds = r.request().postDataJSON().tag_ids
        return r.fulfill({ json: {
          id: 500, title: 'New tagged card', section: 'today', completed: false,
          position: 0, tags: [{ id: 99, name: 'brandnew', color: '#3b82f6' }],
        }})
      }
      return r.fulfill({ json: ALL_TODOS })
    })

    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.column-add-btn').first().click()
    const sheet = page.locator('.card-sheet')
    await sheet.locator('#cs-title').fill('New tagged card')
    await sheet.locator('.tag-input-text').fill('brandnew')
    await sheet.locator('.tag-input-text').press('Enter')
    await expect(sheet.locator('.tag-chip', { hasText: 'brandnew' })).toBeVisible()

    await sheet.getByRole('button', { name: /add card/i }).click()

    await expect.poll(() => postedTagIds).not.toBeNull()
    expect(postedTagIds).toContain(99)
  })
})

// ---------------------------------------------------------------------------
// Edit modal — scheduled_at persistence
// ---------------------------------------------------------------------------
test.describe('breakdown', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('"Break down" tab is visible in the assistant panel', async ({ page }) => {
    await page.route('**/api/cards/3/breakdown', (r) =>
      r.fulfill({ json: { subtasks: [], tag_name: 'Project: Call dentist' } })
    )
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await expect(page.locator('.assist-inline')).toBeVisible()
    await expect(page.getByRole('button', { name: /^break down$/i })).toBeVisible()
  })

  test('clicking "Break down" tab generates subtasks and shows editable list', async ({ page }) => {
    await page.route('**/api/cards/3/breakdown', (r) =>
      r.fulfill({ json: { subtasks: ['Step 1', 'Step 2', 'Step 3'], tag_name: 'Project: Call dentist' } })
    )
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await page.getByRole('button', { name: /^break down$/i }).click()
    await expect(page.getByText('Project: Call dentist')).toBeVisible()
    await expect(page.locator('.assist-bd-input').nth(0)).toHaveValue('Step 1')
    await expect(page.locator('.assist-bd-input').nth(1)).toHaveValue('Step 2')
    await expect(page.locator('.assist-bd-input').nth(2)).toHaveValue('Step 3')
    await expect(page.getByRole('button', { name: /create 3 subtasks/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Mobile assistant modal
// ---------------------------------------------------------------------------
test.describe('mobile assistant modal', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('"✦ Assist" button is visible in the card sheet footer', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    const sheet = page.locator('.card-sheet')
    await expect(sheet).toBeVisible()
    await expect(sheet.getByRole('button', { name: /assist/i })).toBeVisible()
  })

  test('"✦ Assist" button opens the assistant modal', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    await page.locator('.card-sheet').getByRole('button', { name: /assist/i }).click()
    await expect(page.locator('.assist-modal')).toBeVisible()
    await expect(page.locator('.assist-title')).toHaveText('Assistant')
    await expect(page.locator('.assist-tabs')).toBeVisible()
  })

  test('assistant modal is full-screen on mobile', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    await page.locator('.card-sheet').getByRole('button', { name: /assist/i }).click()
    const modal = page.locator('.assist-modal')
    await expect(modal).toBeVisible()
    const box = await modal.boundingBox()
    // Full-screen: should span the full viewport width and start at x=0
    expect(box.x).toBe(0)
    expect(box.width).toBeCloseTo(390, -1)
    // And should be taller than a typical centered modal
    expect(box.height).toBeGreaterThan(600)
  })

  test('"Break down" tab is accessible from the mobile card sheet', async ({ page }) => {
    await page.route('**/api/cards/3/breakdown', r =>
      r.fulfill({ json: { subtasks: [], tag_name: 'Project: Call dentist' } }))
    await page.goto('/board')
    await waitForApp(page)
    await page.locator('.event-card', { hasText: 'Call dentist' }).click({ force: true })
    await page.locator('.card-sheet').getByRole('button', { name: /assist/i }).click()
    await expect(page.getByRole('button', { name: /^break down$/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Project tag visibility
// ---------------------------------------------------------------------------
test.describe('project tag visibility', () => {
  const PROJECT_TAG_DONE   = { id: 10, name: 'Project: Done Project',   color: '#059669', is_project: true }
  const PROJECT_TAG_ACTIVE = { id: 11, name: 'Project: Active Project', color: '#2563eb', is_project: true }

  test('completed project tags are hidden from the sidebar', async ({ page }) => {
    const completedCard = {
      id: 20, title: 'Finished task', section: 'today', completed: true,
      description: null, position: 6, overdue_days: 0, tags: [PROJECT_TAG_DONE],
    }
    await page.route('**/api/tags',  r => r.fulfill({ json: [...TAGS, PROJECT_TAG_DONE] }))
    await page.route('**/api/cards', r => r.fulfill({ json: [...ALL_TODOS, completedCard] }))
    await page.goto('/board')
    await waitForApp(page)
    await expect(page.getByRole('button', { name: 'Project: Done Project', exact: true }))
      .toHaveCount(0)
  })

  test('archived project tags are hidden from the sidebar', async ({ page }) => {
    const archivedCard = {
      id: 21, title: 'Archived task', section: 'today', completed: false, archived: true,
      description: null, position: 6, overdue_days: 0, tags: [PROJECT_TAG_DONE],
    }
    await page.route('**/api/tags',  r => r.fulfill({ json: [...TAGS, PROJECT_TAG_DONE] }))
    await page.route('**/api/cards', r => r.fulfill({ json: [...ALL_TODOS, archivedCard] }))
    await page.goto('/board')
    await waitForApp(page)
    await expect(page.getByRole('button', { name: 'Project: Done Project', exact: true }))
      .toHaveCount(0)
  })

  test('project tags with at least one active card remain visible', async ({ page }) => {
    const activeCard = {
      id: 22, title: 'Ongoing task', section: 'today', completed: false, archived: false,
      description: null, position: 6, overdue_days: 0, tags: [PROJECT_TAG_ACTIVE],
    }
    await page.route('**/api/tags',  r => r.fulfill({ json: [...TAGS, PROJECT_TAG_ACTIVE] }))
    await page.route('**/api/cards', r => r.fulfill({ json: [...ALL_TODOS, activeCard] }))
    await page.goto('/board')
    await waitForApp(page)
    await expect(page.getByRole('button', { name: 'Project: Active Project', exact: true }))
      .toBeVisible()
  })

  test('non-project tags are always visible regardless of card state', async ({ page }) => {
    // "work" tag has completed todos in the mock but should still show
    await page.goto('/board')
    await waitForApp(page)
    await expect(page.getByRole('button', { name: 'work', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'personal', exact: true })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Focus card — project tag shows like any other tag pill
// ---------------------------------------------------------------------------
test.describe('focus card tags', () => {
  test('shows a Project: tag as a normal pill on the focused task', async ({ page }) => {
    const projectTag = { id: 12, name: 'Project: Brunch Planning', color: '#d97706' }
    // Card 2 ("Review pull requests") is the first unscheduled today card — it becomes the focus card
    const todosWithProject = ALL_TODOS.map(t =>
      t.id === 2 ? { ...t, tags: [projectTag] } : t
    )
    await page.route('**/api/tags',  r => r.fulfill({ json: [...TAGS, projectTag] }))
    await page.route('**/api/cards', r => r.fulfill({ json: todosWithProject }))
    await page.goto('/today')
    await waitForApp(page)
    const focusCard = page.locator('.event-card--focus', { hasText: 'Review pull requests' })
    await expect(focusCard).toBeVisible()
    await expect(focusCard.getByText('Project: Brunch Planning')).toBeVisible()
  })

  test('shows no Project: pill when the focused task has no Project: tag', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    const focusCard = page.locator('.event-card--focus', { hasText: 'Review pull requests' })
    await expect(focusCard).toBeVisible()
    await expect(focusCard.getByText(/^Project:/)).toHaveCount(0)
  })
})

// ---------------------------------------------------------------------------
// edit modal scheduled_at
// ---------------------------------------------------------------------------
test.describe('edit modal scheduled_at', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('scheduled date is pre-filled when card has scheduled_at', async ({ page }) => {
    // Card 1 "Daily Engineering Standup" has scheduled_at: '2026-06-03T09:00:00'
    const card = page.locator('.event-card', { hasText: 'Daily Engineering Standup' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    const editBtn = panel.getByRole('button', { name: /^edit$/i })
    await expect(editBtn).toBeVisible()
    await editBtn.click()
    await expect(panel.locator('.cdp-title')).toHaveText(/edit card/i)
    // The datetime-local input should show the pre-filled scheduled date
    await expect(page.locator('#cdp-scheduled')).toHaveValue('2026-06-03T09:00')
  })

  test('scheduled date persists after save (PUT returns updated card)', async ({ page }) => {
    const updatedCard = {
      id: 3, title: 'Call dentist', section: 'today', completed: false,
      scheduled_at: '2026-06-01T10:00:00', description: null, position: 2, overdue_days: 2, tags: [],
    }
    // Mock PUT to return card with scheduled_at set
    await page.route('**/api/cards/3', r => {
      if (r.request().method() === 'PUT') return r.fulfill({ json: updatedCard })
      return r.continue()
    })

    // Open the detail panel for "Call dentist" (no scheduled_at initially)
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /^edit$/i }).click()
    await expect(panel.locator('.cdp-title')).toHaveText(/edit card/i)

    // Set a scheduled date and save
    await page.locator('#cdp-scheduled').fill('2026-06-01T10:00')
    await panel.getByRole('button', { name: /^save$/i }).click()
    // Panel returns to view mode after save
    await expect(panel.locator('.cdp-title')).toHaveText(/call dentist/i)

    // Re-open edit — state should now have scheduled_at from the PUT response
    await panel.getByRole('button', { name: /^edit$/i }).click()
    await expect(panel.locator('.cdp-title')).toHaveText(/edit card/i)
    await expect(page.locator('#cdp-scheduled')).toHaveValue('2026-06-01T10:00')
  })
})

// ---------------------------------------------------------------------------
// Calendar page
// ---------------------------------------------------------------------------
test.describe('calendar page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/calendar')
    await waitForApp(page)
  })

  test('List and Month view toggle buttons are visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: /^list$/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /^month$/i })).toBeVisible()
  })

  test('refresh button is visible', async ({ page }) => {
    await expect(page.locator('.calp-refresh')).toBeVisible()
  })

  test('today badge is shown in list view', async ({ page }) => {
    await expect(page.locator('.calp-today-badge')).toBeVisible()
  })

  test('calendar event appears in list view', async ({ page }) => {
    await expect(page.getByText('Product Review')).toBeVisible()
  })

  test('switching to Month view shows calendar grid with day headers', async ({ page }) => {
    await page.getByRole('button', { name: /^month$/i }).click()
    await expect(page.locator('.calp-month-view')).toBeVisible()
    // Day-of-week headers
    for (const day of ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']) {
      await expect(page.locator('.calp-grid-dow', { hasText: day })).toBeVisible()
    }
  })

  test('discover tab button is visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: /^discover$/i })).toBeVisible()
  })

  test('discover tab shows discovery content when clicked', async ({ page }) => {
    await page.getByRole('button', { name: /^discover$/i }).click()
    await expect(page.locator('.disc-view')).toBeVisible()
  })

  test('discovery panel shows ranked events with match badge', async ({ page }) => {
    await page.route('**/api/discovery/events', r => r.fulfill({ json: [
      {
        id: 'feed1::ev1',
        uid: 'feed1::ev1',
        title: 'Photography Workshop',
        description: 'Learn portrait lighting',
        location: 'Community Arts Center',
        url: null,
        start: '2026-06-06T10:00:00Z',
        end: '2026-06-06T12:00:00Z',
        all_day: false,
        feed_name: 'Meetup SF',
        score: 9,
        reason: 'Hands-on creative workshop, great for meeting artists.',
      },
    ]}))
    await page.goto('/calendar')
    await waitForApp(page)
    await page.getByRole('button', { name: /^discover$/i }).click()
    await expect(page.getByText('Photography Workshop')).toBeVisible()
    await expect(page.locator('.disc-score-badge--high')).toBeVisible()
  })

  test('discovery panel shows hint to add interests when no score present', async ({ page }) => {
    await page.route('**/api/discovery/events', r => r.fulfill({ json: [
      {
        id: 'feed1::ev1',
        uid: 'feed1::ev1',
        title: 'Board Game Night',
        description: null,
        location: null,
        url: null,
        start: '2026-06-07T19:00:00Z',
        end: null,
        all_day: false,
        feed_name: 'Local Events',
        score: null,
        reason: null,
      },
    ]}))
    await page.goto('/calendar')
    await waitForApp(page)
    await page.getByRole('button', { name: /^discover$/i }).click()
    await expect(page.getByText('Board Game Night')).toBeVisible()
    await expect(page.locator('.disc-no-interests-hint')).toBeVisible()
  })

  test('calendar settings modal shows discovery feed inputs and interests textarea', async ({ page }) => {
    await page.route('**/api/calendar/mappings', r => r.fulfill({ json: [] }))
    await page.route('**/api/settings/export-token', r => r.fulfill({ json: '' }))
    await page.route('**/api/discovery/feeds', r => r.fulfill({ json: [
      { id: 1, name: 'Meetup SF', ical_url: 'https://example.com/meetup.ics' },
    ]}))
    await page.route('**/api/discovery/interests', r => r.fulfill({ json: { interests: 'Tech meetups and workshops' } }))
    await page.goto('/calendar')
    await waitForApp(page)
    // Open settings via header gear → Calendar
    await page.locator('button[title="Settings"]').click()
    await page.locator('.settings-dropdown-item', { hasText: /calendar/i }).first().click()
    await expect(page.locator('.cal-feed-name-input').first()).toBeVisible()
    await expect(page.locator('.cal-url-input').first()).toBeVisible()
    await expect(page.locator('.cal-disc-interests')).toHaveValue('Tech meetups and workshops')
  })

  test('calendar settings modal shows a saved max distance and unit', async ({ page }) => {
    await page.route('**/api/calendar/mappings', r => r.fulfill({ json: [] }))
    await page.route('**/api/settings/export-token', r => r.fulfill({ json: '' }))
    await page.route('**/api/discovery/feeds', r => r.fulfill({ json: [] }))
    await page.route('**/api/discovery/interests', r => r.fulfill({ json: {
      // Stored canonically in miles regardless of display unit -- 40mi displays as ~64km.
      interests: '', max_distance_miles: 40, distance_unit: 'km',
    } }))
    await page.goto('/calendar')
    await waitForApp(page)
    await page.locator('button[title="Settings"]').click()
    await page.locator('.settings-dropdown-item', { hasText: /calendar/i }).first().click()
    await expect(page.locator('.cal-disc-distance-input')).toHaveValue('64')
    await expect(page.getByRole('button', { name: 'km' })).toHaveClass(/cal-feed-tag-pill--all/)
  })

  test('saving calendar settings PUTs the max distance converted to miles', async ({ page }) => {
    await page.route('**/api/calendar/mappings', r => r.fulfill({ json: [] }))
    await page.route('**/api/settings/export-token', r => r.fulfill({ json: '' }))
    await page.route('**/api/discovery/feeds', r => r.fulfill({ json: [] }))
    let putBody = null
    await page.route('**/api/discovery/interests', r => {
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: { ok: true } })
      }
      return r.fulfill({ json: { interests: '', max_distance_miles: null, distance_unit: null } })
    })
    await page.goto('/calendar')
    await waitForApp(page)
    await page.locator('button[title="Settings"]').click()
    await page.locator('.settings-dropdown-item', { hasText: /calendar/i }).first().click()
    await page.locator('.cal-disc-distance-input').fill('50')
    await page.getByRole('button', { name: 'km' }).click()
    await page.locator('.btn-save').click()
    await expect.poll(() => putBody).toBeTruthy()
    expect(putBody.distance_unit).toBe('km')
    expect(putBody.max_distance_miles).toBeCloseTo(50 * 0.621371, 2)
  })

  test('discovery event card shows distance when present', async ({ page }) => {
    await page.route('**/api/discovery/events', r => r.fulfill({
      headers: { 'X-Distance-Unit': 'mi' },
      json: [{
        id: 'feed1::ev1',
        uid: 'feed1::ev1',
        title: 'Nearby Meetup',
        description: null,
        location: 'Oakland, CA',
        url: null,
        start: '2026-06-06T10:00:00Z',
        end: null,
        all_day: false,
        feed_name: 'Meetup SF',
        score: null,
        reason: null,
        distance_miles: 8.3,
      }],
    }))
    await page.goto('/calendar')
    await waitForApp(page)
    await page.getByRole('button', { name: /^discover$/i }).click()
    await expect(page.getByText('Nearby Meetup')).toBeVisible()
    await expect(page.getByText('8.3 mi away')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Telegram settings modal
// ---------------------------------------------------------------------------
test.describe('telegram settings modal', () => {
  const baseConfig = {
    bot_token: 'tok', chat_id: '123', schedule_time: '07:30', tz_offset: 0,
    habit_reminder_time: '', overdue_nudge_time: '', weekly_review_schedule_time: 'SUN:18:00',
  }

  test.beforeEach(async ({ page }) => {
    await page.route('**/api/telegram/config', r => {
      if (r.request().method() === 'GET') return r.fulfill({ json: baseConfig })
      return r.fulfill({ json: { ok: true } })
    })
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button[title="Settings"]').click()
    await page.locator('.settings-dropdown-item', { hasText: /telegram/i }).first().click()
  })

  test('shows a weekly review day and time picker', async ({ page }) => {
    const row = page.locator('.telegram-label', { hasText: 'Weekly review' })
    await expect(row.locator('select').first()).toBeVisible()
    await expect(row.locator('select').nth(1)).toBeVisible()
  })

  test('loads the saved weekly review schedule', async ({ page }) => {
    await page.route('**/api/telegram/config', r => {
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: { ...baseConfig, weekly_review_schedule_time: 'WED:09:00' } })
      }
      return r.fulfill({ json: { ok: true } })
    })
    await page.reload()
    await waitForApp(page)
    await page.locator('button[title="Settings"]').click()
    await page.locator('.settings-dropdown-item', { hasText: /telegram/i }).first().click()

    const row = page.locator('.telegram-label', { hasText: 'Weekly review' })
    await expect(row.locator('select').first()).toHaveValue('WED')
    await expect(row.locator('select').nth(1)).toHaveValue('9')
  })

  test('saving PUTs the weekly review schedule as DOW:HH:00', async ({ page }) => {
    let putBody = null
    await page.route('**/api/telegram/config', r => {
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: { ok: true } })
      }
      return r.fulfill({ json: baseConfig })
    })

    const row = page.locator('.telegram-label', { hasText: 'Weekly review' })
    await row.locator('select').first().selectOption('FRI')
    await row.locator('select').nth(1).selectOption('9')
    await page.getByRole('button', { name: /^save$/i }).click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody.weekly_review_schedule_time).toBe('FRI:09:00')
  })

  test('send test weekly review shows a success message', async ({ page }) => {
    await page.route('**/api/telegram/test-weekly-review', r => r.fulfill({ json: { ok: true } }))
    await page.getByRole('button', { name: /send test weekly review/i }).click()
    await expect(page.getByText(/weekly review sent/i)).toBeVisible()
  })

  test('send test weekly review shows an error message on failure', async ({ page }) => {
    await page.route('**/api/telegram/test-weekly-review', r =>
      r.fulfill({ json: { ok: false, error: 'Could not generate weekly review (LLM error).' } }))
    await page.getByRole('button', { name: /send test weekly review/i }).click()
    await expect(page.getByText(/could not generate weekly review/i)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Health page
// ---------------------------------------------------------------------------
test.describe('health page', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/health/correlations', r => r.fulfill({ json: { correlations: [], segments: [], summary: null, weight_n: 0, fat_n: 0 } }))
    await page.route('**/api/health/experiment', r => r.fulfill({ json: null }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [] }))
    await page.route('**/api/food**', r => r.fulfill({ json: [] }))
    await page.route('**/api/workouts**', r => r.fulfill({ json: [] }))
    await page.goto('/health')
    await waitForApp(page)
  })

  test('habit tag chip is shown for a habit that has a tag', async ({ page }) => {
    const card = page.locator('.habit-card', { hasText: 'Evening walk' })
    await expect(card.locator('.habit-card-tag-pill', { hasText: 'personal' })).toBeVisible()
  })

  test('sidebar tag filter narrows the habit list', async ({ page }) => {
    await page.locator('.sidebar-item', { hasText: 'personal' }).click()
    await expect(page).toHaveURL(/\/health\/tag\/2/)
    await expect(page.getByText('Evening walk')).toBeVisible()
    await expect(page.getByText('Morning meditation')).toHaveCount(0)
  })

  test('switching to health from another page preserves the tag selection', async ({ page }) => {
    await page.goto('/board/tag/2')
    await waitForApp(page)
    await page.locator('.sidebar-item', { hasText: 'Habits' }).click()
    await expect(page).toHaveURL(/\/health\/tag\/2/)
  })

  test('"Connect Withings" prompt is shown when not connected', async ({ page }) => {
    await expect(page.locator('.health-not-connected')).toBeVisible()
    await expect(page.locator('.health-not-connected').getByRole('button', { name: /connect withings/i })).toBeVisible()
  })

  test('manual measurement log is visible even when Withings is not connected', async ({ page }) => {
    const section = page.locator('.health-section', { hasText: 'Log a measurement' })
    await expect(page.getByRole('heading', { name: 'Log a measurement' })).toBeVisible()
    await expect(section.getByRole('button', { name: /^log$/i })).toBeVisible()
  })

  test('trip mode: travel mode toggle is off by default in settings', async ({ page }) => {
    await page.locator('button[title="Settings"]').click()
    const item = page.locator('.settings-dropdown-item', { hasText: 'Travel mode' })
    await expect(item).toBeVisible()
    await expect(item.locator('.notif-toggle')).not.toHaveClass(/notif-toggle--on/)
  })

  test('trip mode: toggling on in settings starts a trip', async ({ page }) => {
    let currentTrip = null
    await page.route('**/api/trip', r => {
      if (r.request().method() === 'POST') {
        currentTrip = { id: 1, name: null, start_date: '2026-09-01', end_date: null, retrospective_sent: false }
        return r.fulfill({ json: currentTrip })
      }
      return r.fulfill({ json: currentTrip })
    })
    await page.goto('/health')
    await waitForApp(page)

    await page.locator('button[title="Settings"]').click()
    const item = page.locator('.settings-dropdown-item', { hasText: 'Travel mode' })
    await item.click()

    await expect(item.locator('.notif-toggle')).toHaveClass(/notif-toggle--on/)
    await expect(item).toContainText('since')
  })

  test('trip mode: toggling off in settings ends the active trip', async ({ page }) => {
    let currentTrip = { id: 1, name: null, start_date: '2026-09-01', end_date: null, retrospective_sent: false }
    await page.route('**/api/trip', r => r.fulfill({ json: currentTrip }))
    await page.route('**/api/trip/1/end', r => {
      currentTrip = { ...currentTrip, end_date: '2026-09-06', retrospective_sent: true }
      return r.fulfill({ json: {
        trip: currentTrip,
        retrospective: '<b>Welcome back</b>\n\nGreat trip, and your streaks are safe.',
      } })
    })
    await page.goto('/health')
    await waitForApp(page)

    await page.locator('button[title="Settings"]').click()
    const item = page.locator('.settings-dropdown-item', { hasText: 'Travel mode' })
    await expect(item.locator('.notif-toggle')).toHaveClass(/notif-toggle--on/)

    await item.click()
    await expect(item.locator('.notif-toggle')).not.toHaveClass(/notif-toggle--on/)
    await expect(item).not.toContainText('since')
  })

  test('dismissing and regenerating the weekly experiment refreshes the habits list', async ({ page }) => {
    // Regression test: dismissing archives the old habit and generating the replacement
    // creates a new one server-side -- the Habits list must refetch afterward instead of
    // keeping the now-stale cached habits (the now-archived old one still "active", the
    // new one missing) until some unrelated action happens to refresh it.
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    let habitsRequestCount = 0
    await page.route(/\/api\/habits(\?|$)/, r => {
      habitsRequestCount += 1
      const url = r.request().url()
      return r.fulfill({ json: url.includes('archived=true') ? [] : HABITS })
    })
    await page.route('**/api/health/experiment', async r => {
      if (r.request().method() === 'DELETE') return r.fulfill({ json: { ok: true } })
      return r.fulfill({ json: {
        id: 42, week: '2026-W22', text: 'Row 1.5 mi/day instead of 1 mi/day',
        hypothesis: null, needs_habit: true, habit_id: 10, health_metric: null,
      } })
    })
    await page.goto('/health')
    await waitForApp(page)
    await expect(page.locator('.experiment-card')).toBeVisible()

    const countBeforeDismiss = habitsRequestCount
    await page.getByRole('button', { name: /dismiss & generate new/i }).click()

    await expect.poll(() => habitsRequestCount).toBeGreaterThan(countBeforeDismiss)
  })

  test('logging a manual measurement POSTs the entry and shows it in the list', async ({ page }) => {
    let postBody = null
    await page.route('**/api/health/measurements**', r => {
      if (r.request().method() === 'POST') {
        postBody = r.request().postDataJSON()
        return r.fulfill({ status: 201, json: { id: 42, ...postBody, source: 'manual' } })
      }
      return r.fulfill({ json: { ok: true } })
    })
    // Reflect the newly-added entry on the next health-data reload.
    await page.route('**/api/withings/health-data**', r =>
      r.fulfill({ json: {
        measurements: postBody ? [{ id: 42, ...postBody, source: 'manual' }] : [],
        habit_completions: {},
      } }))

    const section = page.locator('.health-section', { hasText: 'Log a measurement' })
    await section.locator('select').selectOption('weight')
    await section.locator('input[type="number"]').fill('70.5')
    await section.getByRole('button', { name: /^log$/i }).click()

    await expect.poll(() => postBody).not.toBeNull()
    expect(postBody.metric).toBe('weight')
    expect(postBody.value).toBe(70.5)
    await expect(section.locator('.food-entry', { hasText: 'Weight' })).toBeVisible()
  })

  test('deleting a manual measurement entry calls DELETE with its id', async ({ page }) => {
    await page.route('**/api/withings/health-data**', r =>
      r.fulfill({ json: {
        measurements: [{ id: 7, date: '2026-06-01', metric: 'weight', value: 70, source: 'manual' }],
        habit_completions: {},
      } }))
    await page.goto('/health')
    await waitForApp(page)

    let deletedId = null
    await page.route('**/api/health/measurements/7', r => {
      deletedId = 7
      return r.fulfill({ json: { ok: true } })
    })

    const section = page.locator('.health-section', { hasText: 'Log a measurement' })
    await section.locator('.food-entry-delete').click()
    await expect.poll(() => deletedId).toBe(7)
  })

  test('food log input is visible when Withings is connected', async ({ page }) => {
    // Override: Withings connected → showCharts = true → FoodLog renders
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.goto('/health')
    await waitForApp(page)
    await expect(page.locator('.food-input')).toBeVisible()
  })

  test('clicking edit on a food entry shows a pre-filled edit form', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/food**', r => {
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: [{
          id: 1, raw_input: 'coffee', name: 'Coffee', category: 'drink',
          consumed_at: '2026-06-03T08:00:00', notes: null, quality: 8, calories: 5,
        }] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await page.locator('.food-entry-edit').click()
    await expect(page.locator('.food-entry-edit-input')).toHaveValue('Coffee')
    await expect(page.locator('.food-entry-edit-calories')).toHaveValue('5')
    await expect(page.locator('.food-entry-edit-quality')).toHaveValue('8')
  })

  test('saving a food entry edit PUTs the updated fields', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    let putBody = null
    await page.route('**/api/food**', r => {
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: {
          id: 1, raw_input: 'coffee', name: 'Oat milk latte', category: 'drink',
          consumed_at: '2026-06-03T08:00:00', notes: null, quality: 8, calories: 60,
        } })
      }
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: [{
          id: 1, raw_input: 'coffee', name: 'Coffee', category: 'drink',
          consumed_at: '2026-06-03T08:00:00', notes: null, quality: 8, calories: 5,
        }] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await page.locator('.food-entry-edit').click()
    await page.locator('.food-entry-edit-input').fill('Oat milk latte')
    await page.locator('.food-entry-edit-calories').fill('60')
    await page.locator('.food-entry-edit-save').click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody.name).toBe('Oat milk latte')
    expect(putBody.calories).toBe(60)
    await expect(page.locator('.food-entry-edit-form')).toHaveCount(0)
  })

  test('cancelling a food entry edit discards changes', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/food**', r => {
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: [{
          id: 1, raw_input: 'coffee', name: 'Coffee', category: 'drink',
          consumed_at: '2026-06-03T08:00:00', notes: null, quality: 8, calories: 5,
        }] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await page.locator('.food-entry-edit').click()
    await page.locator('.food-entry-edit-input').fill('Something else')
    await page.locator('.food-entry-edit-cancel').click()
    await expect(page.locator('.food-entry-edit-form')).toHaveCount(0)
    await expect(page.locator('.food-entry-name')).toHaveText('Coffee')
  })

  test('food log add posts raw text and reloads the list', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    let postBody = null
    await page.route('**/api/food**', r => {
      if (r.request().method() === 'POST') {
        postBody = r.request().postDataJSON()
        return r.fulfill({ status: 201, json: [{
          id: 1, raw_input: 'coffee', name: 'Coffee', category: 'drink',
          consumed_at: '2026-06-03T08:00:00', notes: null, quality: null, calories: null,
        }] })
      }
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: postBody ? [{
          id: 1, raw_input: 'coffee', name: 'Coffee', category: 'drink',
          consumed_at: '2026-06-03T08:00:00', notes: null, quality: null, calories: null,
        }] : [] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await page.locator('.food-input').fill('coffee')
    await page.locator('.food-input').press('Enter')
    await expect.poll(() => postBody).not.toBeNull()
    expect(postBody.raw_input).toBe('coffee')
    await expect(page.locator('.food-entry-name')).toHaveText('Coffee')
  })

  test('deleting a food entry removes it from the list', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    let deleted = false
    await page.route('**/api/food**', r => {
      if (r.request().method() === 'DELETE') {
        deleted = true
        return r.fulfill({ json: { ok: true } })
      }
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: [{
          id: 1, raw_input: 'coffee', name: 'Coffee', category: 'drink',
          consumed_at: '2026-06-03T08:00:00', notes: null, quality: null, calories: null,
        }] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await expect(page.locator('.food-entry-name')).toHaveText('Coffee')
    await page.locator('.food-entry-delete').click()
    await expect.poll(() => deleted).toBe(true)
    await expect(page.locator('.food-entry')).toHaveCount(0)
  })

  test('workout log add posts raw text and reloads the list', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    let postBody = null
    await page.route('**/api/workouts**', r => {
      const url = r.request().url()
      if (r.request().method() === 'POST') {
        postBody = r.request().postDataJSON()
        return r.fulfill({ status: 201, json: [{
          id: 1, raw_input: 'rowed 5000m', type: 'row', value: 5000, unit: 'm',
          notes: null, logged_at: '2026-06-03T08:00:00',
        }] })
      }
      if (url.includes('/chart')) return r.fulfill({ json: [] })
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: postBody ? [{
          id: 1, raw_input: 'rowed 5000m', type: 'row', value: 5000, unit: 'm',
          notes: null, logged_at: '2026-06-03T08:00:00',
        }] : [] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await page.locator('.workout-input').fill('rowed 5000m')
    await page.locator('.workout-input').press('Enter')
    await expect.poll(() => postBody).not.toBeNull()
    expect(postBody.raw_input).toBe('rowed 5000m')
    await expect(page.locator('.food-entry-name')).toHaveText('row · 5000 m')
  })

  test('deleting a workout entry removes it from the list', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    let deleted = false
    await page.route('**/api/workouts**', r => {
      const url = r.request().url()
      if (r.request().method() === 'DELETE') {
        deleted = true
        return r.fulfill({ json: { ok: true } })
      }
      if (url.includes('/chart')) return r.fulfill({ json: [] })
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: [{
          id: 1, raw_input: 'rowed 5000m', type: 'row', value: 5000, unit: 'm',
          notes: null, logged_at: '2026-06-03T08:00:00',
        }] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await expect(page.locator('.food-entry-name')).toHaveText('row · 5000 m')
    await page.locator('.food-entry-delete').click()
    await expect.poll(() => deleted).toBe(true)
    await expect(page.locator('.food-entry')).toHaveCount(0)
  })

  test('clicking edit on a workout entry shows a pre-filled edit form', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/workouts**', r => {
      const url = r.request().url()
      if (url.includes('/chart')) return r.fulfill({ json: [] })
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: [{
          id: 1, raw_input: 'rowed 5000m', type: 'row', value: 5000, unit: 'm',
          notes: 'Felt good', logged_at: '2026-06-03T08:00:00',
        }] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await page.locator('.food-entry-edit').click()
    await expect(page.locator('.workout-entry-edit-type')).toHaveValue('row')
    await expect(page.locator('.workout-entry-edit-value')).toHaveValue('5000')
    await expect(page.locator('.workout-entry-edit-unit')).toHaveValue('m')
    await expect(page.locator('.workout-entry-edit-notes')).toHaveValue('Felt good')
  })

  test('saving a workout entry edit PUTs the updated fields', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    let putBody = null
    await page.route('**/api/workouts**', r => {
      const url = r.request().url()
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: {
          id: 1, raw_input: 'rowed 5000m', type: 'row', value: 6000, unit: 'm',
          notes: 'Felt good', logged_at: '2026-06-03T08:00:00',
        } })
      }
      if (url.includes('/chart')) return r.fulfill({ json: [] })
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: [{
          id: 1, raw_input: 'rowed 5000m', type: 'row', value: 5000, unit: 'm',
          notes: null, logged_at: '2026-06-03T08:00:00',
        }] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await page.locator('.food-entry-edit').click()
    await page.locator('.workout-entry-edit-value').fill('6000')
    await page.locator('.food-entry-edit-save').click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody.value).toBe(6000)
    await expect(page.locator('.food-entry-edit-form')).toHaveCount(0)
  })

  test('cancelling a workout entry edit discards changes', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/workouts**', r => {
      const url = r.request().url()
      if (url.includes('/chart')) return r.fulfill({ json: [] })
      if (r.request().method() === 'GET') {
        return r.fulfill({ json: [{
          id: 1, raw_input: 'rowed 5000m', type: 'row', value: 5000, unit: 'm',
          notes: null, logged_at: '2026-06-03T08:00:00',
        }] })
      }
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)
    await page.locator('.food-entry-edit').click()
    await page.locator('.workout-entry-edit-value').fill('9999')
    await page.locator('.food-entry-edit-cancel').click()
    await expect(page.locator('.food-entry-edit-form')).toHaveCount(0)
    await expect(page.locator('.food-entry-name')).toHaveText('row · 5000 m')
  })

  test('logging a matching workout auto-checks the linked experiment habit without a manual reload', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    let habitChecked = false
    await page.route(/\/api\/habits(\?|$)/, r => {
      const url = r.request().url()
      if (url.includes('archived=true')) return r.fulfill({ json: [] })
      const habits = HABITS.map(h =>
        h.id === 3 ? { ...h, completed_today: habitChecked } : h
      )
      return r.fulfill({ json: habits })
    })
    await page.route('**/api/workouts**', r => {
      const url = r.request().url()
      if (r.request().method() === 'POST') {
        habitChecked = true
        return r.fulfill({ status: 201, json: [{
          id: 1, raw_input: 'rowed 2mi', type: 'row', value: 2, unit: 'mi',
          notes: null, logged_at: '2026-06-03T08:00:00',
        }] })
      }
      if (url.includes('/chart')) return r.fulfill({ json: [] })
      return r.fulfill({ json: [] })
    })
    await page.goto('/health')
    await waitForApp(page)

    const habitCard = page.locator('.habit-card', { hasText: '1 hour screen-free time' })
    await expect(habitCard.locator('.habit-card-check')).toHaveAttribute('aria-label', 'Mark complete')

    await page.locator('.workout-input').fill('rowed 2mi')
    await page.locator('.workout-input').press('Enter')

    await expect(habitCard).toHaveClass(/habit-card--done/)
    await expect(habitCard.locator('.habit-card-check')).toHaveAttribute('aria-label', 'Mark incomplete')
  })

  test('workout-routine experiment outcome shows a card with baseline vs experiment values', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 42, week: '2026-W22', text: 'Row 2 mi/day instead of 1 mi/day',
        hypothesis: null, action: 'Row 2 mi/day instead of 1 mi/day', status: 'dismissed',
        needs_habit: false, habit_id: null, health_metric: null, health_goal: null,
        weight_delta: null, fat_delta: null, weight_baseline: null, fat_baseline: null,
        habit_completion_rate: null,
        workout_type: 'row', workout_target_value: 2, workout_unit: 'mi',
        workout_baseline_avg: 1.1, workout_experiment_avg: 1.9,
        workout_baseline_n: 10, workout_experiment_n: 5, workout_p: 0.01,
        created_at: '2026-06-01T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    const card = page.locator('.seg-card', { hasText: 'Row' })
    await expect(card).toBeVisible()
    await expect(card.getByText('→ target 2 mi')).toBeVisible()
    await expect(card.locator('.seg-signal')).toHaveText('strong signal')
    await expect(card.getByText('Baseline')).toBeVisible()
    await expect(card.getByText('1.10 mi')).toBeVisible()
    await expect(card.getByText('During experiment')).toBeVisible()
    await expect(card.getByText('1.90 mi')).toBeVisible()
  })

  test('past experiment history shows a workout-based verdict', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 42, week: '2026-W22', text: 'Row 2 mi/day instead of 1 mi/day',
        hypothesis: null, action: 'Row 2 mi/day instead of 1 mi/day', status: 'dismissed',
        needs_habit: false, habit_id: null, health_metric: null, health_goal: null,
        weight_delta: null, fat_delta: null, weight_baseline: null, fat_baseline: null,
        habit_completion_rate: null,
        workout_type: 'row', workout_target_value: 2, workout_unit: 'mi',
        workout_baseline_avg: 1.1, workout_experiment_avg: 1.9,
        workout_baseline_n: 10, workout_experiment_n: 5, workout_p: 0.01,
        created_at: '2026-06-01T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    await page.getByRole('button', { name: /show past experiments/i }).click()
    const row = page.locator('.exp-history-row', { hasText: '2026-W22' })
    await expect(row.locator('.exp-verdict')).toHaveText('Significant change')
  })

  test('workout experiment with no adherence shows "not enough adherence" even when weight data looks favorable', async ({ page }) => {
    // Regression test for a real bug: weight_delta/weight_baseline are
    // populated for every experiment type, so the generic weight/fat
    // verdict branch used to fire before the workout significance test was
    // ever consulted -- a routine that was never actually increased (p not
    // significant, target not met) still got credited with "Better than
    // usual" purely because the weight numbers happened to look good.
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 47, week: '2026-W27', text: 'Row 2 mi/day instead of 1 mi/day',
        hypothesis: null, action: 'Row 2 mi/day instead of 1 mi/day', status: 'dismissed',
        needs_habit: false, habit_id: null, health_metric: null, health_goal: null,
        weight_delta: -0.05, fat_delta: null, weight_baseline: 0.02, fat_baseline: null,
        habit_completion_rate: null,
        workout_type: 'row', workout_target_value: 2, workout_unit: 'mi',
        workout_baseline_avg: 1.1, workout_experiment_avg: 1.15, // barely moved
        workout_baseline_n: 10, workout_experiment_n: 5, workout_p: 0.62, // not significant
        created_at: '2026-07-06T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    await page.getByRole('button', { name: /show past experiments/i }).click()
    const row = page.locator('.exp-history-row', { hasText: '2026-W27' })
    await expect(row.locator('.exp-verdict')).toHaveText('Not enough adherence to judge')
  })

  test('workout experiment with a large calorie shift shows a confound caveat', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 48, week: '2026-W28', text: 'Row 2 mi/day instead of 1 mi/day',
        hypothesis: null, action: 'Row 2 mi/day instead of 1 mi/day', status: 'dismissed',
        needs_habit: false, habit_id: null, health_metric: null, health_goal: null,
        weight_delta: -0.05, fat_delta: null, weight_baseline: 0.02, fat_baseline: null,
        habit_completion_rate: null,
        workout_type: 'row', workout_target_value: 2, workout_unit: 'mi',
        workout_baseline_avg: 1.1, workout_experiment_avg: 2.0,
        workout_baseline_n: 10, workout_experiment_n: 5, workout_p: 0.01,
        confounds: { avg_calories: { baseline: 2000, experiment: 2400 } },
        created_at: '2026-07-13T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    const card = page.locator('.seg-card', { hasText: 'Row' })
    await expect(card.locator('.seg-caveat')).toContainText('Calories also rose ~20%')
  })

  test('workout experiment with a large steps shift shows a steps confound caveat too', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 49, week: '2026-W29', text: 'Row 2 mi/day instead of 1 mi/day',
        hypothesis: null, action: 'Row 2 mi/day instead of 1 mi/day', status: 'dismissed',
        needs_habit: false, habit_id: null, health_metric: null, health_goal: null,
        weight_delta: -0.05, fat_delta: null, weight_baseline: 0.02, fat_baseline: null,
        habit_completion_rate: null,
        workout_type: 'row', workout_target_value: 2, workout_unit: 'mi',
        workout_baseline_avg: 1.1, workout_experiment_avg: 2.0,
        workout_baseline_n: 10, workout_experiment_n: 5, workout_p: 0.01,
        confounds: { avg_steps: { baseline: 6000, experiment: 18000 } },
        created_at: '2026-07-20T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    const card = page.locator('.seg-card', { hasText: 'Row' })
    await expect(card.locator('.seg-caveat')).toContainText('Steps also rose ~200%')
  })

  test('a habit experiment with a large steps shift shows a confound caveat in its history row', async ({ page }) => {
    // Habit-backed experiments (e.g. "sleep 8 hours") previously had no way to show a
    // confound caveat at all -- they never appeared in the routine-outcome card grid, and
    // the plain history row had nothing to show. Now they get the same confound check via
    // the generic all-other-weeks baseline, surfaced right in that history row.
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 50, week: '2026-W30', text: 'Sleep 8 hours a night',
        hypothesis: null, action: 'Sleep 8 hours a night', status: 'dismissed',
        needs_habit: true, habit_id: 12, health_metric: null, health_goal: null,
        weight_delta: -0.05, fat_delta: null, weight_baseline: 0.02, fat_baseline: null,
        habit_completion_rate: 0.86,
        confounds: { avg_steps: { baseline: 6000, experiment: 18000 } },
        created_at: '2026-07-27T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    await page.getByRole('button', { name: /show past experiments/i }).click()
    const row = page.locator('.exp-history-row', { hasText: '2026-W30' })
    await expect(row.locator('.seg-caveat')).toContainText('Steps also rose ~200%')
  })

  test('food-elimination experiment outcome shows a card with before vs during counts', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 43, week: '2026-W23', text: 'Cut out coffee this week',
        hypothesis: null, action: 'Cut out coffee entirely this week', status: 'dismissed',
        needs_habit: true, habit_id: null, health_metric: null, health_goal: null,
        weight_delta: -0.05, fat_delta: null, weight_baseline: 0.02, fat_baseline: null,
        habit_completion_rate: null,
        food_name: 'coffee', food_target_frequency: 0,
        food_baseline_frequency: 4.5, food_experiment_count: 1, food_baseline_weeks_n: 3,
        created_at: '2026-06-08T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    const card = page.locator('.seg-card', { hasText: 'Coffee' })
    await expect(card).toBeVisible()
    await expect(card.getByText('→ target 0x/week')).toBeVisible()
    await expect(card.getByText('adhered', { exact: true })).toBeVisible()
    await expect(card.getByText('Before')).toBeVisible()
    await expect(card.getByText('4.5x/week')).toBeVisible()
    await expect(card.getByText('n=3 weeks')).toBeVisible()
    await expect(card.getByText('During experiment')).toBeVisible()
    await expect(card.getByText('1x')).toBeVisible()

    await page.getByRole('button', { name: /show past experiments/i }).click()
    const row = page.locator('.exp-history-row', { hasText: '2026-W23' })
    await expect(row.locator('.exp-verdict')).toHaveText('Better than usual')
  })

  test('food experiment with low adherence shows "not adhered" and skips the weight/fat verdict', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 44, week: '2026-W24', text: 'Cut out coffee this week',
        hypothesis: null, action: 'Cut out coffee entirely this week', status: 'dismissed',
        needs_habit: true, habit_id: null, health_metric: null, health_goal: null,
        // Weight delta looks "better than usual" on paper, but adherence
        // was never actually achieved -- the verdict must not claim credit.
        weight_delta: -0.05, fat_delta: null, weight_baseline: 0.02, fat_baseline: null,
        habit_completion_rate: null,
        food_name: 'coffee', food_target_frequency: 0,
        food_baseline_frequency: 4.5, food_experiment_count: 4, food_baseline_weeks_n: 3,
        created_at: '2026-06-15T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    const card = page.locator('.seg-card', { hasText: 'Coffee' })
    await expect(card.getByText('not adhered', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: /show past experiments/i }).click()
    const row = page.locator('.exp-history-row', { hasText: '2026-W24' })
    await expect(row.locator('.exp-verdict')).toHaveText('Not enough adherence to judge')
  })

  test('food experiment with a large calorie shift shows a confound caveat', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 45, week: '2026-W25', text: 'Cut out coffee this week',
        hypothesis: null, action: 'Cut out coffee entirely this week', status: 'dismissed',
        needs_habit: true, habit_id: null, health_metric: null, health_goal: null,
        weight_delta: -0.05, fat_delta: null, weight_baseline: 0.02, fat_baseline: null,
        habit_completion_rate: null,
        food_name: 'coffee', food_target_frequency: 0,
        food_baseline_frequency: 4.5, food_experiment_count: 0, food_baseline_weeks_n: 3,
        confounds: { avg_calories: { baseline: 2000, experiment: 1600 } },
        created_at: '2026-06-22T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    const card = page.locator('.seg-card', { hasText: 'Coffee' })
    await expect(card.locator('.seg-caveat')).toContainText('Calories also dropped ~20%')
  })

  test('food experiment with a small calorie shift shows no confound caveat', async ({ page }) => {
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.route('**/api/health/experiments', r => r.fulfill({ json: [
      {
        id: 46, week: '2026-W26', text: 'Cut out coffee this week',
        hypothesis: null, action: 'Cut out coffee entirely this week', status: 'dismissed',
        needs_habit: true, habit_id: null, health_metric: null, health_goal: null,
        weight_delta: -0.05, fat_delta: null, weight_baseline: 0.02, fat_baseline: null,
        habit_completion_rate: null,
        food_name: 'coffee', food_target_frequency: 0,
        food_baseline_frequency: 4.5, food_experiment_count: 0, food_baseline_weeks_n: 3,
        confounds: { avg_calories: { baseline: 2000, experiment: 1950 } },
        created_at: '2026-06-29T00:00:00Z',
      },
    ]}))
    await page.goto('/health')
    await waitForApp(page)
    const card = page.locator('.seg-card', { hasText: 'Coffee' })
    await expect(card.locator('.seg-caveat')).toHaveCount(0)
  })
})

// ---------------------------------------------------------------------------
// Search modal — functional
// ---------------------------------------------------------------------------
test.describe('search modal', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/cards/search**', r =>
      r.fulfill({ json: [
        { id: 1, title: 'Daily Engineering Standup', section: 'today', completed: false,
          archived: false, description: null, tags: [], position: 0, overdue_days: 0 },
      ] })
    )
    await page.goto('/board')
    await waitForApp(page)
    await page.keyboard.press('/')
    await expect(page.getByRole('dialog')).toBeVisible()
  })

  test('search input is auto-focused on open', async ({ page }) => {
    const input = page.locator('[placeholder*="search" i]').or(page.locator('input[type="text"]')).first()
    await expect(input).toBeFocused()
  })

  test('typing a query shows card results', async ({ page }) => {
    await page.keyboard.type('standup')
    await expect(page.getByText('Daily Engineering Standup')).toBeVisible()
  })

  test('results include a section badge', async ({ page }) => {
    await page.keyboard.type('standup')
    // Section label badge should appear (Today / This Week / etc.)
    await expect(page.getByText(/today|this week|this month/i).first()).toBeVisible()
  })

  test('habits appear in search results when name matches', async ({ page }) => {
    // No mock override needed — habit filter is client-side from passed habits list
    await page.keyboard.type('meditation')
    await expect(page.getByText('Morning meditation')).toBeVisible()
  })

  test('calendar events appear in search results when title matches', async ({ page }) => {
    // Calendar events are client-side filtered from the calendarEvents prop
    await page.keyboard.type('product review')
    await expect(page.getByText('Product Review')).toBeVisible()
    await expect(page.getByText('Event')).toBeVisible()
  })
})

test.describe('search modal — mobile card open', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('selecting a card result opens it in the CardSheet bottom sheet', async ({ page }) => {
    await page.route('**/api/cards/search**', r =>
      r.fulfill({ json: [
        { id: 1, title: 'Daily Engineering Standup', section: 'today', completed: false,
          archived: false, description: null, tags: [], position: 0, overdue_days: 0 },
      ] })
    )
    await page.goto('/board')
    await waitForApp(page)
    await page.keyboard.press('/')
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.keyboard.type('standup')
    await expect(page.locator('.search-result', { hasText: 'Daily Engineering Standup' })).toBeVisible()
    await page.keyboard.press('ArrowDown') // selects the first result
    await page.keyboard.press('Enter')
    await expect(page.locator('.card-sheet')).toBeVisible()
    await expect(page.locator('.card-sheet').getByText('Daily Engineering Standup')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Archive section on board
// ---------------------------------------------------------------------------
test.describe('archive section on board', () => {
  test('archive collapsible has no count badge when no completed cards exist', async ({ page }) => {
    // Default mock has no completed cards — count badge only renders when count > 0
    await page.goto('/board')
    await waitForApp(page)
    await expect(page.locator('.archive .collapsible-count')).toHaveCount(0)
  })

  test('completed card appears in archive section with count badge', async ({ page }) => {
    const completedCard = {
      id: 99, title: 'Completed task', section: 'today', completed: true, archived: false,
      description: null, position: 10, overdue_days: 0, tags: [],
    }
    await page.route('**/api/cards', r => r.fulfill({ json: [...ALL_TODOS, completedCard] }))
    await page.goto('/board')
    await waitForApp(page)
    await expect(page.locator('.archive')).toBeVisible()
    await expect(page.locator('.archive .collapsible-count')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Tag filter bar — filtering behavior (mobile only — hidden on desktop)
// ---------------------------------------------------------------------------
test.describe('tag filter bar', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('tag filter bar is visible on mobile board', async ({ page }) => {
    await expect(page.locator('.tag-filter-bar')).toBeVisible()
  })

  test('clicking a tag pill navigates to tag-scoped URL', async ({ page }) => {
    await page.locator('.tag-filter-bar-pill', { hasText: 'work' }).click()
    await expect(page).toHaveURL(/\/board\/tag\/1/)
  })

  test('clicking active tag pill deselects and returns to /board', async ({ page }) => {
    await page.goto('/board/tag/1')
    await waitForApp(page)
    // The active pill may be off-screen on mobile; force click it
    await page.locator('.tag-filter-bar-pill--active').click({ force: true })
    await expect(page).toHaveURL(/\/board$/)
  })

  test('cards without selected tag are hidden when tag is active', async ({ page }) => {
    await page.goto('/board/tag/2')   // personal tag
    await waitForApp(page)
    // Need to switch to Today tab to see today-section cards
    await page.locator('.mobile-tab', { hasText: 'Today' }).click()
    // "Call dentist" has no tags — should not appear
    await expect(page.locator('.event-card', { hasText: 'Call dentist' })).toHaveCount(0)
    // "Read that article" has personal tag (it's in Later)
    await page.locator('.mobile-tab', { hasText: 'Later' }).click()
    await expect(page.locator('.event-card', { hasText: 'Read that article' })).toBeVisible()
  })

  test('"All" pill is shown and navigates to unfiltered board', async ({ page }) => {
    await page.goto('/board/tag/1')
    await waitForApp(page)
    const allPill = page.locator('.tag-filter-bar-pill', { hasText: 'All' })
    await expect(allPill).toBeVisible()
    await allPill.click()
    await expect(page).toHaveURL(/\/board$/)
  })

  test('selecting a second tag pill adds to the selection instead of replacing it', async ({ page }) => {
    await page.locator('.tag-filter-bar-pill', { hasText: 'work' }).click()
    await page.locator('.tag-filter-bar-pill', { hasText: 'personal' }).click({ force: true })
    await expect(page).toHaveURL(/\/board\/tag\/1,2/)
  })

  test('switching pages via mobile nav preserves the tag selection', async ({ page }) => {
    await page.goto('/board/tag/1')
    await waitForApp(page)
    await page.locator('.mobile-nav-item', { hasText: 'Calendar' }).click()
    await expect(page).toHaveURL(/\/calendar\/tag\/1/)
  })
})

// ---------------------------------------------------------------------------
// Sidebar tag filter (desktop)
// ---------------------------------------------------------------------------
test.describe('sidebar tag filter', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('selecting multiple tags shows the union of matching cards', async ({ page }) => {
    await page.locator('.sidebar-item', { hasText: 'work' }).click()
    await page.locator('.sidebar-item', { hasText: 'personal' }).click()
    await expect(page).toHaveURL(/\/board\/tag\/1,2/)
    await expect(page.getByText('Daily Engineering Standup')).toBeVisible()
    await expect(page.getByText('Read that article')).toBeVisible()
    await expect(page.getByText('Call dentist')).toHaveCount(0)
  })

  test('deselecting one tag narrows back to the remaining selection', async ({ page }) => {
    await page.goto('/board/tag/1,2')
    await waitForApp(page)
    await page.locator('.sidebar-item', { hasText: 'work' }).click()
    await expect(page).toHaveURL(/\/board\/tag\/2$/)
  })

  test('active tag shows a checkmark indicator', async ({ page }) => {
    await page.locator('.sidebar-item', { hasText: 'work' }).click()
    await expect(
      page.locator('.sidebar-item', { hasText: 'work' }).locator('.sidebar-tag-check--on')
    ).toBeVisible()
  })

  test('switching pages via sidebar preserves the tag selection', async ({ page }) => {
    await page.goto('/board/tag/1')
    await waitForApp(page)
    await page.locator('.sidebar-item', { hasText: 'Calendar' }).click()
    await expect(page).toHaveURL(/\/calendar\/tag\/1/)
  })

  test('"All" clears a multi-tag selection', async ({ page }) => {
    await page.goto('/board/tag/1,2')
    await waitForApp(page)
    await page.locator('.sidebar-item', { hasText: 'All' }).click()
    await expect(page).toHaveURL(/\/board$/)
  })
})

// ---------------------------------------------------------------------------
// Assistant modal — context picker
// ---------------------------------------------------------------------------
test.describe('assistant modal — context picker', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await expect(page.locator('.assist-inline')).toBeVisible()
  })

  test('context panel is collapsible and labelled "Context"', async ({ page }) => {
    await expect(page.locator('.assist-context-toggle')).toContainText('Context')
  })

  test('context source select is visible when context panel is expanded', async ({ page }) => {
    await page.locator('.assist-context-toggle').click()
    await expect(page.locator('.assist-context-source-select')).toBeVisible()
  })

  test('selecting a source loads context text into the textarea', async ({ page }) => {
    await page.locator('.assist-context-toggle').click()
    await page.locator('.assist-context-source-select').selectOption('section:today')
    await expect(page.locator('.assist-context-input')).toHaveValue(/Today/)
    await expect(page.locator('.assist-context-loaded-note')).toContainText('Today')
  })

  test('textarea placeholder mentions loading cards or pasting text', async ({ page }) => {
    await page.locator('.assist-context-toggle').click()
    const placeholder = await page.locator('.assist-context-input').getAttribute('placeholder')
    expect(placeholder).toMatch(/load cards|paste/i)
  })
})

// ---------------------------------------------------------------------------
// Assistant modal — web search status indicator
// ---------------------------------------------------------------------------
test.describe('assistant modal — web search indicator', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('"Searching the web…" text appears when status=searching SSE event fires', async ({ page }) => {
    const searchingSse =
      'data: {"status":"searching"}\n\n' +
      'data: {"text":"Here are some brunch spots."}\n\n' +
      'data: [DONE]\n\n'
    await page.route('**/api/cards/*/thread/message', r => r.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: searchingSse,
    }))
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await page.locator('.assist-input').fill('Find me brunch spots')
    await page.locator('.assist-send').click()
    await expect(page.getByText('Here are some brunch spots.')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Assistant modal — "Create tasks" error state
// ---------------------------------------------------------------------------
test.describe('assistant modal — create tasks error', () => {
  test('shows error message when breakdown commit fails', async ({ page }) => {
    await page.route('**/api/cards/3/breakdown', r =>
      r.fulfill({ json: { subtasks: ['Step 1', 'Step 2', 'Step 3'], tag_name: 'Project: Call dentist' } })
    )
    await page.route('**/api/cards/3/breakdown/commit', r =>
      r.fulfill({ status: 500, json: { detail: 'Server error' } })
    )
    await page.goto('/board')
    await waitForApp(page)
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const panel = page.locator('.card-detail-panel')
    await panel.getByRole('button', { name: /assist/i }).click()
    await page.getByRole('button', { name: /^break down$/i }).click()
    await expect(page.locator('.assist-bd-input').first()).toBeVisible()
    await page.getByRole('button', { name: /create 3 subtasks/i }).click()
    await expect(page.locator('.assist-bd-error')).toBeVisible()
    // Should still be on the confirm screen (not closed)
    await expect(page.locator('.assist-bd-input').first()).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Insights panel — habit snooze
// ---------------------------------------------------------------------------
test.describe('insights panel — habit snooze', () => {
  const HABIT_INSIGHT = {
    type: 'habit_trend',
    text: 'Evening walk completed only 2/7 days — try to build consistency.',
    completions_last_7: 2,
    habit_id: 2,
    habit_name: 'Evening walk',
  }

  test.beforeEach(async ({ page }) => {
    await page.route('**/api/insights', r => r.fulfill({ json: [HABIT_INSIGHT] }))
    await page.addInitScript(() => localStorage.removeItem('insights_snooze'))
    await page.goto('/today')
    await waitForApp(page)
  })

  test('habit insight is shown with dismiss button', async ({ page }) => {
    await expect(page.locator('.insight-card--habit')).toBeVisible()
    await expect(page.locator('.insight-card--habit .insight-dismiss')).toBeVisible()
  })

  test('clicking dismiss reveals snooze options', async ({ page }) => {
    await page.locator('.insight-card--habit .insight-dismiss').click()
    await expect(page.getByRole('button', { name: /snooze tomorrow/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /snooze 3 days/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /^dismiss$/i })).toBeVisible()
  })

  test('clicking "Dismiss" hides the insight for the session', async ({ page }) => {
    await page.locator('.insight-card--habit .insight-dismiss').click()
    await page.getByRole('button', { name: /^dismiss$/i }).click()
    await expect(page.locator('.insight-card--habit')).toHaveCount(0)
  })

  test('snoozing hides insight and persists to localStorage', async ({ page }) => {
    await page.locator('.insight-card--habit .insight-dismiss').click()
    await page.getByRole('button', { name: /snooze tomorrow/i }).click()
    await expect(page.locator('.insight-card--habit')).toHaveCount(0)
    const stored = await page.evaluate(() => localStorage.getItem('insights_snooze'))
    expect(stored).not.toBeNull()
    const parsed = JSON.parse(stored)
    expect(Object.keys(parsed)).toContain('habit-2')
  })

  test('snoozed habit insight does not reappear on page reload', async ({ page }) => {
    const futureDate = new Date()
    futureDate.setDate(futureDate.getDate() + 3)
    const exp = futureDate.toISOString().slice(0, 10)
    // Register AFTER the beforeEach removeItem script so it runs second and wins
    await page.addInitScript((exp) => {
      localStorage.setItem('insights_snooze', JSON.stringify({ 'habit-2': exp }))
    }, exp)
    await page.goto('/today')
    await waitForApp(page)
    await expect(page.locator('.insight-card--habit')).toHaveCount(0)
  })
})

// ---------------------------------------------------------------------------
// Insights panel — completion pattern insight
// ---------------------------------------------------------------------------
test.describe('insights panel — completion pattern', () => {
  test('completion pattern insight is shown with green accent', async ({ page }) => {
    await page.route('**/api/insights', r => r.fulfill({ json: [{
      type: 'completion_pattern',
      text: 'You finish most tasks before noon — protect your mornings from meetings.',
      peak_window: 'morning',
      peak_pct: 0.62,
    }]}))
    await page.goto('/today')
    await waitForApp(page)
    await expect(page.locator('.insight-card--pattern')).toBeVisible()
    await expect(page.getByText(/protect your mornings/i)).toBeVisible()
  })

  test('completion pattern insight can be dismissed', async ({ page }) => {
    await page.route('**/api/insights', r => r.fulfill({ json: [{
      type: 'completion_pattern',
      text: 'You finish most tasks before noon — protect your mornings from meetings.',
      peak_window: 'morning',
      peak_pct: 0.62,
    }]}))
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('.insight-card--pattern .insight-dismiss').click()
    await expect(page.locator('.insight-card--pattern')).toHaveCount(0)
  })
})

// ---------------------------------------------------------------------------
// Insights panel — tag filtering
// ---------------------------------------------------------------------------
test.describe('insights panel — tag filtering', () => {
  // Card id 1 ("Daily Engineering Standup") is tagged "work" (id 1) in TODOS.
  const STUCK_INSIGHT = {
    type: 'stuck_task',
    text: 'This task has been sitting in Today for a while.',
    days_stuck: 4,
    card: {
      id: 1, title: 'Daily Engineering Standup', section: 'today', completed: false,
      scheduled_at: null, description: null, position: 0,
      tags: [{ id: 1, name: 'work', color: '#3b82f6' }],
    },
  }
  // Habit id 2 ("Evening walk") is tagged "personal" (id 2) in HABITS.
  const HABIT_INSIGHT = {
    type: 'habit_trend',
    text: 'Evening walk completed only 2/7 days — try to build consistency.',
    completions_last_7: 2,
    habit_id: 2,
    habit_name: 'Evening walk',
  }

  test.beforeEach(async ({ page }) => {
    await page.route('**/api/insights', r => r.fulfill({ json: [STUCK_INSIGHT, HABIT_INSIGHT] }))
  })

  test('both insights are shown when no tag filter is active', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await expect(page.locator('.insight-card--stuck')).toBeVisible()
    await expect(page.locator('.insight-card--habit')).toBeVisible()
  })

  test('stuck task insight is hidden when the tag filter excludes its card', async ({ page }) => {
    await page.goto('/today/tag/2') // personal — the stuck card is tagged work
    await waitForApp(page)
    await expect(page.locator('.insight-card--stuck')).toHaveCount(0)
  })

  test('stuck task insight is shown when the tag filter includes its card', async ({ page }) => {
    await page.goto('/today/tag/1') // work
    await waitForApp(page)
    await expect(page.locator('.insight-card--stuck')).toBeVisible()
  })

  test('habit trend insight is hidden when the tag filter excludes its habit', async ({ page }) => {
    await page.goto('/today/tag/1') // work — the habit is tagged personal
    await waitForApp(page)
    await expect(page.locator('.insight-card--habit')).toHaveCount(0)
  })

  test('habit trend insight is shown when the tag filter includes its habit', async ({ page }) => {
    await page.goto('/today/tag/2') // personal
    await waitForApp(page)
    await expect(page.locator('.insight-card--habit')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Card detail panel — GitHub context + spec + bridge
// ---------------------------------------------------------------------------
test.describe('card detail panel — github and spec', () => {
  // Card linked to a GitHub issue with a spec already set
  const GH_CARD = {
    id: 99, title: 'OAuth login feature',
    description: 'Developer notes here',
    section: 'today', completed: false, archived: false, position: 10,
    tags: [], external_id: 'github:owner/repo/issues/42',
    spec: '## Problem Statement\nLogin is broken.\n\n## Acceptance Criteria\n- [ ] Users can log in',
    updated_at: '2026-06-03T08:00:00Z', created_at: '2026-06-03T08:00:00Z',
  }

  const GH_ENG_ITEMS = [
    {
      id: 10, external_id: 'github:owner/repo/issues/42',
      title: 'OAuth login feature', item_type: 'issue',
      repo: 'owner/repo', number: 42,
      url: 'https://github.com/owner/repo/issues/42',
      state: 'open', body: 'When user clicks login nothing happens.',
      comments: [
        { id: 1, author: 'alice', body: 'We should use PKCE flow.', created_at: '2026-06-01T10:00:00Z' },
        {
          id: 2, author: 'coderabbitai[bot]', body: 'Consider using a set for O(1) lookups.',
          created_at: '2026-06-01T11:00:00Z', comment_type: 'pr_review_comment',
          diff_path: 'src/auth.js', diff_line: 42,
        },
        {
          id: 3, author: 'bob', body: 'Should this handle the empty-token case too?',
          created_at: '2026-06-01T12:00:00Z', comment_type: 'pr_review_comment',
          diff_path: 'src/auth.js', diff_line: 58,
        },
      ],
    },
  ]

  test.beforeEach(async ({ page }) => {
    // Override cards and engineering items for these tests
    await page.route('**/api/cards', r => r.fulfill({ json: [...ALL_TODOS, GH_CARD] }))
    // Stateful: a dismiss PATCH flips this set, and the items route reflects it on the
    // next refetch -- lets tests exercise the real dismiss -> refetch -> re-render loop
    // instead of just asserting the PATCH request shape.
    const dismissedIds = new Set()
    await page.route('**/api/engineering/items', r => r.fulfill({
      json: GH_ENG_ITEMS.map(item => ({
        ...item,
        comments: item.comments.map(c => ({ ...c, dismissed: dismissedIds.has(c.id) })),
      })),
    }))
    await page.route('**/api/engineering/comments/*/dismiss', async r => {
      const id = Number(r.request().url().match(/comments\/(\d+)\/dismiss/)[1])
      const { dismissed } = r.request().postDataJSON()
      if (dismissed) dismissedIds.add(id)
      else dismissedIds.delete(id)
      return r.fulfill({ json: { id, dismissed, dismissed_at: dismissed ? '2026-06-01T12:00:00Z' : null } })
    })
    await page.route('**/api/cards/*/spec/generate', r =>
      r.fulfill({ json: { spec: '## Problem Statement\nGenerated spec content.' } }))
    await page.route('**/api/bridge/jobs', r =>
      r.fulfill({ json: { id: 1, card_id: 99, status: 'pending', result: null,
                          created_at: '2026-06-03T10:00:00Z', updated_at: null } }))
    await page.route('**/api/bridge/jobs/card/*/latest', r =>
      r.fulfill({ json: { job: null } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: null, companion: null } }))
    await page.route('**/api/cards/*/thread', r =>
      r.fulfill({ json: { messages: [], context: '', output: null } }))
    await page.goto('/board')
    await waitForApp(page)
    // Open the detail panel for the GitHub-linked card
    const card = page.locator('.event-card', { hasText: 'OAuth login feature' })
    await card.click()
    await expect(page.locator('.card-detail-panel')).toBeVisible()
  })

  test('GitHub context section is visible for linked cards', async ({ page }) => {
    await expect(page.locator('.cdp-gh-header')).toBeVisible()
  })

  test('GitHub issue type badge is shown', async ({ page }) => {
    await expect(page.locator('.cdp-gh-type')).toBeVisible()
  })

  test('GitHub link is rendered', async ({ page }) => {
    const link = page.locator('.cdp-gh-link')
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', /github\.com/)
  })

  test('CodeRabbit PR review comment shows a badge and diff position', async ({ page }) => {
    const comment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await expect(comment.locator('.cdp-gh-comment-badge')).toHaveText('CodeRabbit')
    await expect(comment.locator('.cdp-gh-comment-position')).toHaveText('src/auth.js:42')
  })

  test('regular issue comment shows no CodeRabbit badge or diff position', async ({ page }) => {
    const comment = page.locator('.cdp-gh-comment', { hasText: 'We should use PKCE flow' })
    await expect(comment.locator('.cdp-gh-comment-badge')).toHaveCount(0)
    await expect(comment.locator('.cdp-gh-comment-position')).toHaveCount(0)
  })

  test('human reviewer PR comment shows diff position but no CodeRabbit badge', async ({ page }) => {
    const comment = page.locator('.cdp-gh-comment', { hasText: 'empty-token case' })
    await expect(comment.locator('.cdp-gh-comment-badge')).toHaveCount(0)
    await expect(comment.locator('.cdp-gh-comment-position')).toHaveText('src/auth.js:58')
  })

  test('review feedback and conversation comments render in separate sections', async ({ page }) => {
    await expect(page.locator('.cdp-gh-review-feedback-title')).toHaveText('Review Feedback')
    await expect(page.locator('.cdp-gh-review-feedback .cdp-gh-comment')).toHaveCount(2)
    await expect(page.locator('.cdp-gh-comments .cdp-gh-comment')).toHaveCount(1)
    await expect(page.locator('.cdp-gh-comments .cdp-gh-comment')).toContainText('PKCE flow')
  })

  test('dismissing a review comment hides it and shows a restore toggle', async ({ page }) => {
    const comment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await comment.locator('.cdp-gh-comment-dismiss').click()

    await expect(page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })).toHaveCount(0)
    const toggle = page.getByRole('button', { name: /show 1 dismissed/i })
    await expect(toggle).toBeVisible()

    await toggle.click()
    const restored = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await expect(restored).toBeVisible()
    await expect(restored).toHaveClass(/cdp-gh-comment--dismissed/)
  })

  test('restoring a dismissed comment removes the dismissed styling', async ({ page }) => {
    const comment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await comment.locator('.cdp-gh-comment-dismiss').click()
    await page.getByRole('button', { name: /show 1 dismissed/i }).click()

    const dismissedComment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await dismissedComment.locator('.cdp-gh-comment-dismiss').click()

    await expect(page.locator('.cdp-gh-review-feedback-toggle')).toHaveCount(0)
    await expect(page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' }))
      .not.toHaveClass(/cdp-gh-comment--dismissed/)
  })

  test('checkboxes appear only on non-dismissed review comments', async ({ page }) => {
    const comment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await expect(comment.locator('.cdp-gh-comment-select')).toBeVisible()

    await comment.locator('.cdp-gh-comment-dismiss').click()
    await page.getByRole('button', { name: /show 1 dismissed/i }).click()
    const dismissedComment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await expect(dismissedComment).toHaveClass(/cdp-gh-comment--dismissed/)
    await expect(dismissedComment.locator('.cdp-gh-comment-select')).toHaveCount(0)
  })

  test('selecting review comments shows a Fix N selected button that updates with the count', async ({ page }) => {
    await expect(page.locator('.cdp-gh-review-feedback-fix')).toHaveCount(0)

    const first = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await first.locator('.cdp-gh-comment-select').check()
    await expect(page.locator('.cdp-gh-review-feedback-fix')).toContainText('Fix 1 selected')

    const second = page.locator('.cdp-gh-comment', { hasText: 'empty-token case' })
    await second.locator('.cdp-gh-comment-select').check()
    await expect(page.locator('.cdp-gh-review-feedback-fix')).toContainText('Fix 2 selected')

    await first.locator('.cdp-gh-comment-select').uncheck()
    await expect(page.locator('.cdp-gh-review-feedback-fix')).toContainText('Fix 1 selected')
  })

  test('dismissing a selected comment removes it from the fix selection', async ({ page }) => {
    const comment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await comment.locator('.cdp-gh-comment-select').check()
    await expect(page.locator('.cdp-gh-review-feedback-fix')).toContainText('Fix 1 selected')

    await comment.locator('.cdp-gh-comment-dismiss').click()
    await expect(page.locator('.cdp-gh-review-feedback-fix')).toHaveCount(0)
  })

  test('requesting a fix with no resumable bridge job shows an inline error', async ({ page }) => {
    // beforeEach already mocks the latest bridge job as null
    const comment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await comment.locator('.cdp-gh-comment-select').check()
    await page.locator('.cdp-gh-review-feedback-fix').click()
    await expect(page.locator('.cdp-gh-review-feedback-error')).toContainText(/run the assistant/i)
  })

  test('requesting a fix with a resumable job queues it and opens the Code tab with status', async ({ page }) => {
    // Stateful: the "latest job" route reflects the fix job once queued, mirroring the
    // real fetch -> queue -> refetch loop that drives AssistModal's Code tab into view.
    let latestJob = {
      id: 5, card_id: 99, status: 'done', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'Implemented feature', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/latest', r => r.fulfill({ json: { job: latestJob } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: latestJob, companion: null } }))
    let fixRequestBody = null
    await page.route('**/api/bridge/jobs/5/fix', async r => {
      fixRequestBody = r.request().postDataJSON()
      latestJob = {
        id: 6, card_id: 99, status: 'pending', target_repo: 'owner/repo',
        branch_name: 'qtask/99-oauth-login', agent_name: null,
        worktree_path: '/tmp/worktrees/99', result: null, output: null,
        spec_snapshot: null, resumes_job_id: 5, fix_comment_ids: [2],
        created_at: '2026-06-03T10:00:00Z', updated_at: null,
      }
      return r.fulfill({ json: latestJob })
    })

    const comment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await comment.locator('.cdp-gh-comment-select').check()
    await page.locator('.cdp-gh-review-feedback-fix').click()

    await expect(page.locator('.assist-spec-tab')).toBeVisible()
    await expect(page.locator('.cdp-bridge-label')).toContainText(/waiting for agent to apply fixes/i)
    expect(fixRequestBody).toEqual({ comment_ids: [2] })
  })

  test('requesting a fix targets the root job, not a newer companion job in another repo', async ({ page }) => {
    // Regression test: review comments belong to the card's own linked repo (root's repo).
    // A companion job targeting a different repo is always newer than root -- "Fix N
    // selected" must not silently resume the companion's worktree just because it's the
    // most recently created job for this card.
    const root = {
      id: 5, card_id: 99, status: 'done', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99-root', result: 'Implemented feature', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    const companion = {
      id: 9, card_id: 99, status: 'done', target_repo: 'owner/web-repo',
      branch_name: 'qtask/99-web', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99-companion', result: 'Wired up the UI', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      depends_on_job_id: 5, created_at: '2026-06-02T09:00:00Z', updated_at: '2026-06-02T09:30:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/latest', r => r.fulfill({ json: { job: companion } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root, companion } }))
    let fixRequestBody = null
    let fixedJobId = null
    await page.route('**/api/bridge/jobs/*/fix', async r => {
      fixedJobId = Number(r.request().url().match(/jobs\/(\d+)\/fix/)[1])
      fixRequestBody = r.request().postDataJSON()
      return r.fulfill({ json: { ...root, id: 6, status: 'pending', resumes_job_id: 5, fix_comment_ids: [2] } })
    })

    const comment = page.locator('.cdp-gh-comment', { hasText: 'Consider using a set' })
    await comment.locator('.cdp-gh-comment-select').check()
    await page.locator('.cdp-gh-review-feedback-fix').click()

    await expect(page.locator('.assist-spec-tab')).toBeVisible()
    expect(fixedJobId).toBe(5)
    expect(fixRequestBody).toEqual({ comment_ids: [2] })
  })

  test('Code tab in Assistant shows spec', async ({ page }) => {
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await expect(page.locator('.cdp-spec-markdown')).toBeVisible()
    await expect(page.locator('.cdp-spec-markdown')).toContainText(/Problem Statement/i)
  })

  test('Generate/Regen button is visible in Code tab', async ({ page }) => {
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await expect(page.locator('.cdp-spec-gen-btn')).toBeVisible()
  })

  test('Run button is visible in Code tab when spec exists', async ({ page }) => {
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await expect(page.locator('.cdp-spec-bridge-btn')).toBeVisible()
    await expect(page.locator('.cdp-spec-bridge-btn')).toContainText(/Run/i)
  })

  test('Copy button is visible in Code tab when spec exists', async ({ page }) => {
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    const copyBtn = page.locator('.assist-spec-tab .cdp-gh-btn', { hasText: /Copy/i })
    await expect(copyBtn).toBeVisible()
  })

  test('branch name field shows the auto-generated default as a placeholder', async ({ page }) => {
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    const input = page.locator('.cdp-branch-input')
    await expect(input).toBeVisible()
    await expect(input).toHaveValue('')
    await expect(input).toHaveAttribute('placeholder', 'qtask/99-oauth-login-feature')
  })

  test('queuing without touching the branch field omits branch_name from the request', async ({ page }) => {
    let requestBody = null
    await page.route('**/api/bridge/jobs', async r => {
      requestBody = r.request().postDataJSON()
      return r.fulfill({ json: { id: 1, card_id: 99, status: 'pending', result: null,
                                 created_at: '2026-06-03T10:00:00Z', updated_at: null } })
    })
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await page.locator('.cdp-spec-bridge-btn').click()

    expect(requestBody).toEqual({ card_id: 99 })
  })

  test('typing a custom branch name sends it in the queue request', async ({ page }) => {
    let requestBody = null
    await page.route('**/api/bridge/jobs', async r => {
      requestBody = r.request().postDataJSON()
      return r.fulfill({ json: { id: 1, card_id: 99, status: 'pending', result: null,
                                 created_at: '2026-06-03T10:00:00Z', updated_at: null } })
    })
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await page.locator('.cdp-branch-input').fill('my-custom-branch')
    await page.locator('.cdp-spec-bridge-btn').click()

    expect(requestBody).toEqual({ card_id: 99, branch_name: 'my-custom-branch' })
  })

  test('a branch name with whitespace shows an inline error and does not queue', async ({ page }) => {
    let requestFired = false
    await page.route('**/api/bridge/jobs', async r => {
      requestFired = true
      return r.fulfill({ json: { id: 1, card_id: 99, status: 'pending', result: null,
                                 created_at: '2026-06-03T10:00:00Z', updated_at: null } })
    })
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await page.locator('.cdp-branch-input').fill('has a space')
    await page.locator('.cdp-spec-bridge-btn').click()

    await expect(page.locator('.cdp-spec-error')).toContainText(/whitespace/i)
    expect(requestFired).toBe(false)
  })

  test('a branch name starting with a dash shows an inline error and does not queue', async ({ page }) => {
    let requestFired = false
    await page.route('**/api/bridge/jobs', async r => {
      requestFired = true
      return r.fulfill({ json: { id: 1, card_id: 99, status: 'pending', result: null,
                                 created_at: '2026-06-03T10:00:00Z', updated_at: null } })
    })
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await page.locator('.cdp-branch-input').fill('-not-a-flag')
    await page.locator('.cdp-spec-bridge-btn').click()

    await expect(page.locator('.cdp-spec-error')).toContainText(/can't start with/i)
    expect(requestFired).toBe(false)
  })

  test('clearing a typed branch name falls back to the auto-generated default', async ({ page }) => {
    let requestBody = null
    await page.route('**/api/bridge/jobs', async r => {
      requestBody = r.request().postDataJSON()
      return r.fulfill({ json: { id: 1, card_id: 99, status: 'pending', result: null,
                                 created_at: '2026-06-03T10:00:00Z', updated_at: null } })
    })
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    const input = page.locator('.cdp-branch-input')
    await input.fill('my-custom-branch')
    await input.fill('')
    await expect(input).toHaveValue('')
    await expect(input).toHaveAttribute('placeholder', 'qtask/99-oauth-login-feature')

    await page.locator('.cdp-spec-bridge-btn').click()
    expect(requestBody).toEqual({ card_id: 99 })
  })

  test('branch field stays editable while a job is running, showing its real current name', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'running', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    const input = page.locator('.cdp-branch-input')
    await expect(input).toHaveValue('qtask/99-oauth-login')
    await expect(input).toBeEnabled()
  })

  test('branch field is locked once a job is done', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'done', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'https://github.com/owner/repo/pull/7', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    await expect(page.locator('.cdp-branch-input')).toBeDisabled()
  })

  test('editing and clicking away from the branch field while running requests a rename', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'running', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    let requestBody = null
    await page.route('**/api/bridge/jobs/5/request-rename', async r => {
      requestBody = r.request().postDataJSON()
      return r.fulfill({ json: { ...job, requested_branch_name: requestBody.branch_name } })
    })
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    const input = page.locator('.cdp-branch-input')
    await input.fill('qtask/99-better-name')
    await page.locator('.cdp-section-label', { hasText: 'Brief' }).click()  // blur

    await expect.poll(() => requestBody).toEqual({ branch_name: 'qtask/99-better-name' })
  })

  test('clicking away without changing the branch name does not request a rename', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'running', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    let requestFired = false
    await page.route('**/api/bridge/jobs/5/request-rename', async r => {
      requestFired = true
      return r.fulfill({ json: job })
    })
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    const input = page.locator('.cdp-branch-input')
    await expect(input).toHaveValue('qtask/99-oauth-login')
    await input.click()
    await page.locator('.cdp-section-label', { hasText: 'Brief' }).click()  // blur, unchanged

    expect(requestFired).toBe(false)
  })

  test('Resume button appears for an errored job with a resumable worktree', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'error', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'claude exited with code 1', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/latest', r => r.fulfill({ json: { job } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await expect(page.locator('.cdp-bridge-resume-btn')).toBeVisible()
  })

  test('Resume button appears for a stalled job too', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'stalled', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/latest', r => r.fulfill({ json: { job } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await expect(page.locator('.cdp-bridge-resume-btn')).toBeVisible()
  })

  test('Resume button is absent for a done job', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'done', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'Implemented feature', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/latest', r => r.fulfill({ json: { job } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await expect(page.locator('.cdp-bridge-resume-btn')).toHaveCount(0)
  })

  test('a needs_confirmation job shows the flagged paths and a Mark reviewed button', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'needs_confirmation', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'Implemented feature', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      checkpoint_matched_paths: ['alembic/versions/0001_add_x.py'],
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/latest', r => r.fulfill({ json: { job } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    await expect(page.locator('.cdp-bridge-status--needs_confirmation')).toBeVisible()
    await expect(page.locator('.cdp-bridge-checkpoint-paths')).toContainText('alembic/versions/0001_add_x.py')
    await expect(page.getByRole('button', { name: /mark reviewed/i })).toBeVisible()
    await expect(page.locator('.cdp-bridge-resume-btn').filter({ hasText: /resume/i })).toHaveCount(0)
  })

  test('clicking Mark reviewed acknowledges the job and updates the status', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'needs_confirmation', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'Implemented feature', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      checkpoint_matched_paths: ['alembic/versions/0001_add_x.py'],
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    let acknowledgeCalled = false
    await page.route('**/api/bridge/jobs/5/acknowledge', async r => {
      acknowledgeCalled = true
      return r.fulfill({ json: { ...job, status: 'done', checkpoint_matched_paths: null } })
    })
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await page.getByRole('button', { name: /mark reviewed/i }).click()

    expect(acknowledgeCalled).toBe(true)
    await expect(page.locator('.cdp-bridge-status--done')).toBeVisible()
    await expect(page.getByRole('button', { name: /mark reviewed/i })).toHaveCount(0)
  })

  test('a needs_confirmation job flagged by self-review shows the self-review note and fallback label', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'needs_confirmation', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: '', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      checkpoint_matched_paths: [], self_review_flagged: true,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/latest', r => r.fulfill({ json: { job } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    await expect(page.locator('.cdp-bridge-status--needs_confirmation')).toContainText(/self-review flagged possible issues/i)
    await expect(page.locator('.cdp-bridge-self-review-flag')).toBeVisible()
    await expect(page.locator('.cdp-bridge-checkpoint-paths')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /mark reviewed/i })).toBeVisible()
  })

  test('clicking Resume queues a resume job and updates the status label', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'stalled', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/latest', r => r.fulfill({ json: { job } }))
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    let resumeCalled = false
    await page.route('**/api/bridge/jobs/5/resume', async r => {
      resumeCalled = true
      return r.fulfill({
        json: {
          id: 6, card_id: 99, status: 'pending', target_repo: 'owner/repo',
          branch_name: 'qtask/99-oauth-login', agent_name: null,
          worktree_path: '/tmp/worktrees/99', result: null, output: null,
          spec_snapshot: null, resumes_job_id: 5, fix_comment_ids: null,
          created_at: '2026-06-03T10:00:00Z', updated_at: null,
        },
      })
    })

    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await page.locator('.cdp-bridge-resume-btn').click()

    await expect(page.locator('.cdp-bridge-label')).toContainText(/waiting for agent to resume/i)
    expect(resumeCalled).toBe(true)
  })

  test('no attempt history is shown on a first attempt', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'running', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r => r.fulfill({
      json: { root: job, companion: null, attempts: { number: 1, prior_count: 0, prior_failed_count: 0 } },
    }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    await expect(page.locator('.cdp-bridge-attempts')).toHaveCount(0)
  })

  test('attempt history is shown once a prior attempt failed', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'error', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'claude exited with code 1', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r => r.fulfill({
      json: { root: job, companion: null, attempts: { number: 3, prior_count: 2, prior_failed_count: 2 } },
    }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    const attempts = page.locator('.cdp-bridge-attempts')
    await expect(attempts).toBeVisible()
    await expect(attempts).toHaveText('Attempt 3 · all 2 previous attempts failed')
    await expect(attempts).toHaveClass(/cdp-bridge-attempts--failed/)
  })

  test('attempt history without a failure omits the failed-warning styling', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'running', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r => r.fulfill({
      json: { root: job, companion: null, attempts: { number: 2, prior_count: 1, prior_failed_count: 0 } },
    }))
    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    const attempts = page.locator('.cdp-bridge-attempts')
    await expect(attempts).toHaveText('Attempt 2')
    await expect(attempts).not.toHaveClass(/cdp-bridge-attempts--failed/)
  })

  test('companion job button appears once a root job exists, and opens the repo form', async ({ page }) => {
    const job = {
      id: 5, card_id: 99, status: 'done', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'Implemented feature', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: job, companion: null } }))
    await page.route('**/api/bridge/repos', r => r.fulfill({ json: { repos: ['owner/web-repo'] } }))

    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    const addBtn = page.locator('.cdp-companion-add-btn')
    await expect(addBtn).toBeVisible()
    await addBtn.click()
    await expect(page.locator('.cdp-companion-repo-input')).toBeVisible()
  })

  test('queuing a companion job shows its own status row alongside the root job', async ({ page }) => {
    const rootJob = {
      id: 5, card_id: 99, status: 'done', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'Implemented feature', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: rootJob, companion: null } }))
    await page.route('**/api/bridge/repos', r => r.fulfill({ json: { repos: [] } }))
    let companionRequestBody = null
    await page.route('**/api/bridge/jobs', async r => {
      companionRequestBody = r.request().postDataJSON()
      return r.fulfill({ json: {
        id: 6, card_id: 99, status: 'blocked', target_repo: 'owner/web-repo',
        branch_name: null, agent_name: null, worktree_path: null, result: null, output: null,
        spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
        depends_on_job_id: 5, created_at: '2026-06-03T10:00:00Z', updated_at: null,
      } })
    })

    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()
    await page.locator('.cdp-companion-add-btn').click()
    await page.locator('.cdp-companion-repo-input').fill('owner/web-repo')
    await page.locator('.cdp-companion-add--open button', { hasText: 'Queue' }).click()

    expect(companionRequestBody).toEqual({
      card_id: 99, target_repo: 'owner/web-repo', depends_on_job_id: 5,
    })
    await expect(page.locator('.cdp-bridge-companion-repo')).toHaveText('owner/web-repo')
    await expect(page.locator('.cdp-bridge-status--blocked .cdp-bridge-label'))
      .toContainText(/waiting on owner\/repo to finish/i)
  })

  test('a stalled companion job gets its own Resume button', async ({ page }) => {
    const rootJob = {
      id: 5, card_id: 99, status: 'done', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'Implemented feature', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:30:00Z',
    }
    const companionJob = {
      id: 6, card_id: 99, status: 'stalled', target_repo: 'owner/web-repo',
      branch_name: 'qtask/99-web', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99-companion', result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      depends_on_job_id: 5, created_at: '2026-06-02T09:00:00Z', updated_at: '2026-06-02T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: rootJob, companion: companionJob } }))
    let resumeCalled = null
    await page.route('**/api/bridge/jobs/6/resume', async r => {
      resumeCalled = true
      return r.fulfill({ json: { ...companionJob, id: 7, status: 'pending', resumes_job_id: 6 } })
    })

    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    await expect(page.locator('.cdp-bridge-status--stalled .cdp-bridge-resume-btn')).toBeVisible()
    await page.locator('.cdp-bridge-status--stalled .cdp-bridge-resume-btn').click()

    expect(resumeCalled).toBe(true)
    await expect(page.locator('.cdp-bridge-status--stalled')).toHaveCount(0)
  })

  test('cannot start a companion job while the root job has failed', async ({ page }) => {
    const rootJob = {
      id: 5, card_id: 99, status: 'error', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'claude exited with code 1', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: rootJob, companion: null } }))

    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    await expect(page.locator('.cdp-companion-add-btn')).toHaveCount(0)
    await expect(page.locator('.cdp-companion-note')).toContainText(/fix or resume this job/i)
  })

  test('a companion blocked on a failed root reads as stuck, not still waiting', async ({ page }) => {
    const rootJob = {
      id: 5, card_id: 99, status: 'error', target_repo: 'owner/repo',
      branch_name: 'qtask/99-oauth-login', agent_name: 'claude',
      worktree_path: '/tmp/worktrees/99', result: 'claude exited with code 1', output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      created_at: '2026-06-01T09:00:00Z', updated_at: '2026-06-01T09:05:00Z',
    }
    const companionJob = {
      id: 6, card_id: 99, status: 'blocked', target_repo: 'owner/web-repo',
      branch_name: null, agent_name: null, worktree_path: null, result: null, output: null,
      spec_snapshot: GH_CARD.spec, resumes_job_id: null, fix_comment_ids: null,
      depends_on_job_id: 5, created_at: '2026-06-02T09:00:00Z', updated_at: null,
    }
    await page.route('**/api/bridge/jobs/card/*/chain', r =>
      r.fulfill({ json: { root: rootJob, companion: companionJob } }))

    await page.locator('.cdp-btn--assist-footer').click()
    await page.locator('.assist-tab', { hasText: 'Code' }).click()

    await expect(page.locator('.cdp-bridge-status--blocked .cdp-bridge-label'))
      .toContainText(/failed and needs to be fixed or resumed/i)
  })

  test('refresh button is shown in GitHub header', async ({ page }) => {
    // Refresh button is within the GitHub actions area
    const actions = page.locator('.cdp-gh-actions')
    await expect(actions).toBeVisible()
    await expect(actions.getByRole('button').first()).toBeVisible()
  })

  test('GitHub panel can be collapsed and expanded', async ({ page }) => {
    // Content is visible initially (expanded)
    await expect(page.locator('.cdp-gh-content')).toBeVisible()
    // Click collapse toggle
    await page.locator('.cdp-gh-actions').getByRole('button').last().click()
    // Content collapses
    await expect(page.locator('.cdp-gh-content')).not.toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Bridge history section on the plain card detail view
// ---------------------------------------------------------------------------
test.describe('card detail panel — bridge history', () => {
  const HISTORY_CARD = {
    id: 150, title: 'Fix ranking bug',
    description: 'Some notes', section: 'today', completed: false, archived: false, position: 5,
    tags: [], updated_at: '2026-06-03T08:00:00Z', created_at: '2026-06-03T08:00:00Z',
  }

  test.beforeEach(async ({ page }) => {
    await page.route('**/api/cards', r => r.fulfill({ json: [...ALL_TODOS, HISTORY_CARD] }))
    await page.route('**/api/cards/*/thread', r =>
      r.fulfill({ json: { messages: [], context: '', output: null } }))
  })

  async function openHistoryCard(page) {
    await page.goto('/board')
    await waitForApp(page)
    const card = page.locator('.event-card', { hasText: 'Fix ranking bug' })
    await card.click()
    await expect(page.locator('.card-detail-panel')).toBeVisible()
  }

  test('section is hidden when there is no bridge history', async ({ page }) => {
    await page.route('**/api/bridge/jobs/card/150/history', r => r.fulfill({ json: { jobs: [] } }))
    await openHistoryCard(page)
    await expect(page.locator('.cdp-history-list')).toHaveCount(0)
  })

  test('shows one row per job with status, branch, and timestamp', async ({ page }) => {
    await page.route('**/api/bridge/jobs/card/150/history', r => r.fulfill({ json: { jobs: [
      {
        id: 1, status: 'done', target_repo: null, branch_name: 'qtask/150-fix-ranking',
        agent_name: 'claude', result: 'Fixed it', diff_summary: 'src/rank.py | 4 +-',
        checkpoint_matched_paths: null, self_review_flagged: null,
        depends_on_job_id: null, resumes_job_id: null, fix_comment_ids: null,
        created_at: '2026-06-02T09:00:00Z', updated_at: '2026-06-02T09:10:00Z',
      },
    ] } }))
    await openHistoryCard(page)

    const rows = page.locator('.cdp-history-row')
    await expect(rows).toHaveCount(1)
    await expect(rows.first().locator('.cdp-history-dot--done')).toBeVisible()
    await expect(rows.first()).toContainText('qtask/150-fix-ranking')
    await expect(rows.first()).toContainText('Fresh run')
  })

  test('result markdown renders as real HTML, not literal ## text', async ({ page }) => {
    await page.route('**/api/bridge/jobs/card/150/history', r => r.fulfill({ json: { jobs: [
      {
        id: 1, status: 'done', target_repo: null, branch_name: 'qtask/150-fix-ranking',
        agent_name: 'claude', result: '## Verification\n\n### Tests\n\n**passed**',
        diff_summary: null, checkpoint_matched_paths: null, self_review_flagged: null,
        depends_on_job_id: null, resumes_job_id: null, fix_comment_ids: null,
        created_at: '2026-06-02T09:00:00Z', updated_at: '2026-06-02T09:10:00Z',
      },
    ] } }))
    await openHistoryCard(page)

    await expect(page.locator('.cdp-history-result h2', { hasText: 'Verification' })).toBeVisible()
    await expect(page.locator('.cdp-history-result')).not.toContainText('##')
  })

  test("a companion job's row shows its target_repo", async ({ page }) => {
    await page.route('**/api/bridge/jobs/card/150/history', r => r.fulfill({ json: { jobs: [
      {
        id: 2, status: 'running', target_repo: 'owner/web-repo', branch_name: 'qtask/150-fix-ranking',
        agent_name: null, result: null, diff_summary: null,
        checkpoint_matched_paths: null, self_review_flagged: null,
        depends_on_job_id: 1, resumes_job_id: null, fix_comment_ids: null,
        created_at: '2026-06-02T10:00:00Z', updated_at: null,
      },
    ] } }))
    await openHistoryCard(page)

    await expect(page.locator('.cdp-history-repo')).toContainText('owner/web-repo')
    await expect(page.locator('.cdp-history-row')).toContainText('Companion')
  })

  test('diff_summary renders in a monospace block', async ({ page }) => {
    await page.route('**/api/bridge/jobs/card/150/history', r => r.fulfill({ json: { jobs: [
      {
        id: 1, status: 'done', target_repo: null, branch_name: 'qtask/150-fix-ranking',
        agent_name: 'claude', result: null, diff_summary: 'src/rank.py | 4 +-',
        checkpoint_matched_paths: null, self_review_flagged: null,
        depends_on_job_id: null, resumes_job_id: null, fix_comment_ids: null,
        created_at: '2026-06-02T09:00:00Z', updated_at: '2026-06-02T09:10:00Z',
      },
    ] } }))
    await openHistoryCard(page)

    await expect(page.locator('.cdp-history-diff')).toHaveText('src/rank.py | 4 +-')
  })
})

// ---------------------------------------------------------------------------
// GitHub / Engineering settings modal
// ---------------------------------------------------------------------------
test.describe('github settings modal', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('GitHub settings modal opens from settings menu with form fields', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()
    await expect(page.getByRole('heading', { name: /engineering/i }).or(page.getByRole('dialog'))).toBeVisible()
    // Token input and repo list textarea
    await expect(page.locator('[placeholder*="token" i], input[type="password"], input[type="text"]').first()).toBeVisible()
  })

  test('"Add rule" button is visible for repo tags when tags exist', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()
    await expect(page.getByRole('button', { name: /add rule/i })).toBeVisible()
  })

  test('clicking "Add rule" shows a pattern input and tag chips', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()
    await page.getByRole('button', { name: /add rule/i }).click()
    await expect(page.getByPlaceholder(/owner or owner\/repo/i)).toBeVisible()
    await expect(page.locator('.gh-repo-tag-chip', { hasText: 'work' })).toBeVisible()
    await expect(page.locator('.gh-repo-tag-chip', { hasText: 'personal' })).toBeVisible()
  })

  test('clicking a tag chip marks it active and removing the rule hides it', async ({ page }) => {
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()
    await page.getByRole('button', { name: /add rule/i }).click()
    const chip = page.locator('.gh-repo-tag-chip', { hasText: 'work' })
    await chip.click()
    await expect(chip).toHaveClass(/gh-repo-tag-chip--active/)

    await page.locator('.gh-repo-tags-remove').click()
    await expect(page.getByPlaceholder(/owner or owner\/repo/i)).toHaveCount(0)
  })

  test('existing repo tag rules load from config', async ({ page }) => {
    await page.route('**/api/engineering/repo-tags', r => {
      if (r.request().method() === 'GET') return r.fulfill({ json: { 'owner/repo': [1] } })
      return r.fulfill({ json: { ok: true } })
    })
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()
    await expect(page.locator('.gh-repo-tags-pattern')).toHaveValue('owner/repo')
    await expect(page.locator('.gh-repo-tag-chip--active', { hasText: 'work' })).toBeVisible()
  })

  test('saving a repo tag rule PUTs the pattern-to-tag-id mapping', async ({ page }) => {
    let putBody = null
    await page.route('**/api/engineering/repo-tags', r => {
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: { ok: true } })
      }
      return r.fulfill({ json: {} })
    })
    await page.route('**/api/engineering/config', r => {
      if (r.request().method() === 'GET') return r.fulfill({ json: { configured: true, repos: [] } })
      return r.fulfill({ json: { ok: true } })
    })

    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()
    await page.getByRole('button', { name: /add rule/i }).click()
    await page.getByPlaceholder(/owner or owner\/repo/i).fill('trainsit')
    await page.locator('.gh-repo-tag-chip', { hasText: 'work' }).click()
    await page.getByRole('button', { name: /save & sync/i }).click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody).toEqual({ trainsit: [1] })
  })

  test('a rule left with an empty pattern is dropped from the saved payload', async ({ page }) => {
    let putBody = null
    await page.route('**/api/engineering/repo-tags', r => {
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: { ok: true } })
      }
      return r.fulfill({ json: {} })
    })
    await page.route('**/api/engineering/config', r => {
      if (r.request().method() === 'GET') return r.fulfill({ json: { configured: true, repos: [] } })
      return r.fulfill({ json: { ok: true } })
    })

    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()
    await page.getByRole('button', { name: /add rule/i }).click()
    // Leave the pattern input blank, only toggle a tag
    await page.locator('.gh-repo-tag-chip', { hasText: 'work' }).click()
    await page.getByRole('button', { name: /save & sync/i }).click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody).toEqual({})
  })

  test('checkpoint patterns textarea loads saved patterns and shows the save control', async ({ page }) => {
    await page.route('**/api/bridge/checkpoint-patterns', r => {
      if (r.request().method() === 'GET') return r.fulfill({ json: { patterns: ['alembic/versions/*', 'package.json'] } })
      return r.fulfill({ json: { ok: true } })
    })
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()

    await expect(page.getByPlaceholder(/alembic\/versions/i)).toHaveValue('alembic/versions/*\npackage.json')
    await expect(page.getByRole('button', { name: /save patterns/i })).toBeVisible()
  })

  test('saving checkpoint patterns PUTs the newline-split, trimmed list', async ({ page }) => {
    let putBody = null
    await page.route('**/api/bridge/checkpoint-patterns', r => {
      if (r.request().method() === 'PUT') {
        putBody = r.request().postDataJSON()
        return r.fulfill({ json: { ok: true } })
      }
      return r.fulfill({ json: { patterns: [] } })
    })
    await page.getByRole('button', { name: /settings/i }).click()
    await page.getByRole('menuitem', { name: /engineering.*github/i }).click()

    await page.getByPlaceholder(/alembic\/versions/i).fill('alembic/versions/*\n  package.json  \n\nGemfile')
    await page.getByRole('button', { name: /save patterns/i }).click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody).toEqual({ patterns: ['alembic/versions/*', 'package.json', 'Gemfile'] })
  })
})
