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
    id: 1, name: 'Morning meditation', completed_today: true, streak: 7,
    tags: [], recurrence_rule: 'daily',
  },
  {
    id: 2, name: 'Evening walk', completed_today: false, streak: 3,
    tags: [{ id: 2, name: 'personal', color: '#10b981' }], recurrence_rule: 'daily',
  },
]

const CALENDAR_EVENTS = [
  {
    id: 'ev1', title: 'Product Review', section: 'today',
    start: '2026-06-03T14:00:00', end: '2026-06-03T15:00:00',
    all_day: false, description: 'Weekly product review', location: 'Conference Room B',
  },
]

const NOTES = [
  {
    id: 1, title: 'Shopping list',
    content: '- [ ] Milk\n- [ ] Eggs\n- [x] Bread\n- [ ] Coffee',
    tags: [{ id: 2, name: 'personal', color: '#10b981' }],
    updated_at: '2026-06-03T08:00:00Z', created_at: '2026-06-03T08:00:00Z',
  },
  {
    id: 2, title: 'Sprint ideas',
    content: 'Next sprint candidates:\n\n- Improve search\n- Add dark mode option\n- Performance pass',
    tags: [{ id: 1, name: 'work', color: '#3b82f6' }],
    updated_at: '2026-06-02T16:00:00Z', created_at: '2026-06-02T16:00:00Z',
  },
]

// ---------------------------------------------------------------------------
// Shared setup
// ---------------------------------------------------------------------------
async function mockAPIs(page) {
  await page.route('**/api/auth/check', r =>
    r.fulfill({ json: { authed: true, enabled: false } }))

  await page.route('**/api/todos', r => r.fulfill({ json: TODOS }))
  await page.route('**/api/tags', r => r.fulfill({ json: TAGS }))
  await page.route('**/api/calendar-events', r => r.fulfill({ json: CALENDAR_EVENTS }))
  await page.route('**/api/calendar-mappings', r => r.fulfill({ json: {} }))
  await page.route('**/api/habits', r => r.fulfill({ json: HABITS }))
  await page.route('**/api/notes', r => r.fulfill({ json: NOTES }))

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
    await expect(page.getByRole('button', { name: /add/i }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /search/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /settings/i })).toBeVisible()

    // Sidebar nav (desktop) or mobile nav
    for (const label of ['Today', 'Tasks', 'Habits', 'Notes']) {
      await expect(page.getByRole('button', { name: label }).or(page.getByText(label)).first()).toBeVisible()
    }
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

  test('schedule section with mocked tasks and event', async ({ page }) => {
    await expect(page.getByText('Daily Engineering Standup')).toBeVisible()
    await expect(page.getByText('Product Review')).toBeVisible()
  })

  test('tasks section', async ({ page }) => {
    await expect(page.getByText('Review pull requests')).toBeVisible()
    await expect(page.getByText('Call dentist')).toBeVisible()
  })

  test('habits section', async ({ page }) => {
    await expect(page.getByText('Morning meditation')).toBeVisible()
    await expect(page.getByText('Evening walk')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Tasks board
// ---------------------------------------------------------------------------
test.describe('tasks board', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/tasks')
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
// Notes page
// ---------------------------------------------------------------------------
test.describe('notes page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/notes')
    await waitForApp(page)
  })

  test('page heading and new-note button', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Notes' })).toBeVisible()
    await expect(page.getByRole('button', { name: /new note/i })).toBeVisible()
  })

  test('note cards are rendered', async ({ page }) => {
    await expect(page.getByText('Shopping list')).toBeVisible()
    await expect(page.getByText('Sprint ideas')).toBeVisible()
  })

  test('note card content is previewed', async ({ page }) => {
    await expect(page.getByText(/Milk/)).toBeVisible()
    await expect(page.getByText(/Improve search/)).toBeVisible()
  })

  test('tag chips are visible on cards', async ({ page }) => {
    await expect(page.getByText('personal').first()).toBeVisible()
    await expect(page.getByText('work').first()).toBeVisible()
  })

  test('action buttons present on each card', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Edit note' }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'Promote to task' }).first()).toBeVisible()
  })

  test('new note modal opens', async ({ page }) => {
    await page.getByRole('button', { name: /new note/i }).click()
    await expect(page.getByRole('heading', { name: /new note/i })).toBeVisible()
    await expect(page.getByLabel(/title/i)).toBeVisible()
    // Button says "Create" for new notes
    await expect(page.getByRole('button', { name: /create/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Habits page
// ---------------------------------------------------------------------------
test.describe('habits page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/habits')
    await waitForApp(page)
  })

  test('page heading and add button', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Habits' })).toBeVisible()
    await expect(page.getByRole('button', { name: /add habit/i })).toBeVisible()
  })

  test('habit cards are rendered', async ({ page }) => {
    await expect(page.getByText('Morning meditation')).toBeVisible()
    await expect(page.getByText('Evening walk')).toBeVisible()
  })

  test('completion toggle buttons are present', async ({ page }) => {
    await expect(page.getByRole('button', { name: /mark incomplete/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /mark complete/i })).toBeVisible()
  })

  test('edit and delete buttons are present', async ({ page }) => {
    await expect(page.getByRole('button', { name: /edit habit/i }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /delete habit/i }).first()).toBeVisible()
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
})

// ---------------------------------------------------------------------------
// Mobile header layout
// ---------------------------------------------------------------------------
test.describe('mobile header layout', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('weather widget is left-aligned on mobile', async ({ page }) => {
    await page.goto('/')
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
    await expect(page.getByRole('button', { name: /add/i }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /settings/i })).toBeVisible()
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
