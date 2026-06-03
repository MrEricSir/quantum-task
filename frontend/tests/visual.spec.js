/**
 * Visual regression tests.
 *
 * Strategy:
 *   - All API calls are mocked — no backend required.
 *   - The clock is frozen to a fixed date so date strings never change.
 *   - On pushes to main, CI runs with --update-snapshots and commits the
 *     result as the new baseline.
 *   - On PRs, CI compares against the committed baseline and fails on drift.
 *
 * To update snapshots locally after an intentional UI change:
 *   cd frontend && npx playwright test --update-snapshots
 */

import { test, expect } from '@playwright/test'

// ---------------------------------------------------------------------------
// Fixed date — keeps "Tuesday, June 3" stable in the Today header
// ---------------------------------------------------------------------------
const FIXED_DATE = new Date('2026-06-03T10:00:00')

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

  // Block background video so screenshots are pixel-stable (no video frame variability)
  await page.route(/\.(mp4|webm|ogg)(\?.*)?$/, r => r.abort())

  await page.route('**/api/todos', r => r.fulfill({ json: TODOS }))
  await page.route('**/api/tags', r => r.fulfill({ json: TAGS }))
  await page.route('**/api/calendar-events', r => r.fulfill({ json: CALENDAR_EVENTS }))
  await page.route('**/api/calendar-mappings', r => r.fulfill({ json: {} }))
  await page.route('**/api/habits', r => r.fulfill({ json: HABITS }))
  await page.route('**/api/notes', r => r.fulfill({ json: NOTES }))

  // Briefing SSE: send weather then close immediately
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

// Wait for the app shell to render (avoids networkidle issues with SSE connections)
async function waitForApp(page) {
  await page.waitForSelector('.app-header', { state: 'visible' })
  // Pause background video so consecutive screenshots are stable
  await page.evaluate(() => document.querySelectorAll('video').forEach(v => { v.pause(); v.currentTime = 0 }))
  // Wait for briefing spinner to disappear (hidden = not in DOM OR not visible)
  // On pages without a briefing this resolves immediately
  await page.waitForSelector('.briefing-spinner', { state: 'hidden', timeout: 10000 }).catch(() => {})
  // Give React one more tick to flush any remaining state updates
  await page.waitForTimeout(300)
}

test.beforeEach(async ({ page }) => {
  // Freeze the date so "Tuesday, June 3" is always rendered
  await page.clock.setSystemTime(FIXED_DATE)
  // Clear any persisted queue state from previous test runs
  await page.addInitScript(() => localStorage.clear())
  await mockAPIs(page)
})

// ---------------------------------------------------------------------------
// Desktop (1280 × 800)
// ---------------------------------------------------------------------------
test.describe('desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('today page', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await expect(page).toHaveScreenshot('desktop-today.png', { animations: 'disabled' })
  })

  test('tasks board', async ({ page }) => {
    await page.goto('/tasks')
    await waitForApp(page)
    await expect(page).toHaveScreenshot('desktop-tasks.png', { animations: 'disabled' })
  })

  test('notes page', async ({ page }) => {
    await page.goto('/notes')
    await waitForApp(page)
    await expect(page).toHaveScreenshot('desktop-notes.png', { animations: 'disabled' })
  })

  test('habits page', async ({ page }) => {
    await page.goto('/habits')
    await waitForApp(page)
    await expect(page).toHaveScreenshot('desktop-habits.png', { animations: 'disabled' })
  })
})

// ---------------------------------------------------------------------------
// Mobile (390 × 844 — iPhone 14)
// ---------------------------------------------------------------------------
test.describe('mobile', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('today page', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await expect(page).toHaveScreenshot('mobile-today.png', { animations: 'disabled' })
  })

  test('notes page', async ({ page }) => {
    await page.goto('/notes')
    await waitForApp(page)
    await expect(page).toHaveScreenshot('mobile-notes.png', { animations: 'disabled' })
  })

  test('quick-add modal', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await page.waitForSelector('.quick-modal')
    await expect(page).toHaveScreenshot('mobile-quickadd.png', { animations: 'disabled' })
  })
})
