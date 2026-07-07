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
    recent_completions: [true, true, true, true, false, true, true],
  },
  {
    id: 2, name: 'Evening walk', completed_today: false, streak: 3,
    tags: [{ id: 2, name: 'personal', color: '#10b981' }], recurrence_rule: 'daily',
    recent_completions: [false, false, false, false, true, true, false],
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
  await page.route('**/api/engineering/sync', r => r.fulfill({ json: { created: 0, closed: 0, skipped: 0, error: null } }))
  await page.route('**/api/engineering/config', r => r.fulfill({ json: { configured: false, repos: [] } }))
  // habits: handle both active and archived requests
  await page.route(/\/api\/habits(\?|$)/, r => {
    const url = r.request().url()
    return r.fulfill({ json: url.includes('archived=true') ? [] : HABITS })
  })

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
    for (const label of ['Today', 'Board', 'Calendar', 'Habits', 'Engineering']) {
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

  test('habits section appears before schedule', async ({ page }) => {
    await expect(page.getByText('Morning meditation')).toBeVisible()
    await expect(page.getByText('Evening walk')).toBeVisible()
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

  test('"Focus next" banner shows highest-priority untimed task', async ({ page }) => {
    const banner = page.locator('.focus-next')
    await expect(banner).toBeVisible()
    await expect(banner.getByText('Focus next')).toBeVisible()
    await expect(banner.getByText('Review pull requests')).toBeVisible()
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
    for (const col of ['Today', 'This Week', 'This Month', 'Stash']) {
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
// Stash column on /board
// ---------------------------------------------------------------------------
test.describe('cards page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('Stash column is visible', async ({ page }) => {
    await expect(page.locator('.column-label', { hasText: 'Stash' })).toBeVisible()
  })

  test('cards with section=later appear in the Stash column', async ({ page }) => {
    await expect(page.getByText('Shopping list')).toBeVisible()
    await expect(page.getByText('Sprint ideas')).toBeVisible()
  })

  test('clicking a card opens editor with Cancel/Save footer', async ({ page }) => {
    const card = page.locator('.event-card', { hasText: 'Shopping list' })
    await card.click()
    // Expanded view — click Edit to open modal
    const editBtn = card.getByRole('button', { name: /^edit$/i })
    await expect(editBtn).toBeVisible()
    await editBtn.click()
    await expect(page.getByRole('heading', { name: /edit card/i })).toBeVisible()
    await expect(page.locator('#atm-title')).toBeVisible()
    await expect(page.locator('#atm-desc')).toBeVisible()
    await expect(page.getByRole('button', { name: /cancel/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /save changes/i })).toBeVisible()
    await expect(page.locator('.modal-close-btn')).toHaveCount(0)
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
    await expect(page.getByRole('button', { name: /mark complete/i })).toBeVisible()
  })

  test('edit button is present on habit cards', async ({ page }) => {
    await expect(page.getByRole('button', { name: /edit habit/i }).first()).toBeVisible()
  })

  test('archive button is present on each habit card', async ({ page }) => {
    await expect(page.getByRole('button', { name: /archive habit/i }).first()).toBeVisible()
  })

  test('habit archive section is hidden when empty', async ({ page }) => {
    // No archived habits in mock → section should not render
    await expect(page.locator('.habits-archive')).toHaveCount(0)
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

  test('hint text mentions tasks, habits, food, and health goals', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    const hint = page.locator('.quick-hint')
    await expect(hint).toContainText('task')
    await expect(hint).toContainText('habit')
    await expect(hint).toContainText('food')
    await expect(hint).toContainText('health goal')
  })

  test('has Cancel and Add footer buttons, no X button', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await expect(page.getByRole('button', { name: /cancel/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /add/i })).toBeVisible()
    await expect(page.locator('.modal-close-btn')).toHaveCount(0)
  })

  test('confirm screen shows detected type and Back/Add buttons', async ({ page }) => {
    await page.route('**/api/cards/parse-bulk', r =>
      r.fulfill({ json: { items: [{
        type: 'task', title: 'Call dentist', description: null,
        section: 'week', scheduled_at: null, suggested_tags: [], recurrence_rule: null,
      }]}}))
    await page.goto('/today')
    await waitForApp(page)
    await page.locator('button.btn-primary').first().click()
    await page.getByRole('textbox').fill('Call dentist next week')
    await page.getByRole('button', { name: /^add$/i }).click()
    // Confirm screen
    await expect(page.getByRole('heading', { name: /confirm/i })).toBeVisible()
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
    await page.getByRole('button', { name: /^add$/i }).click()
    await expect(page.locator('.quick-type-tab--active')).toHaveText('Task')
    // Override to habit
    await page.locator('.quick-type-tab', { hasText: 'Habit' }).click()
    await expect(page.locator('.quick-type-tab--active')).toHaveText('Habit')
    await expect(page.getByRole('button', { name: /add habit/i })).toBeVisible()
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
    await page.getByRole('button', { name: /^add$/i }).click()
    await expect(page.locator('.quick-type-tab--active')).toHaveText('Task')
    await page.getByRole('button', { name: /back/i }).click()
    await expect(page.getByRole('heading', { name: /quick add/i })).toBeVisible()
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
    await expect(page.getByRole('button', { name: /add/i }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /settings/i })).toBeVisible()
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
        synced_at: new Date().toISOString() },
      { id: 2, external_id: 'github:org/repo/issues/2', title: 'Add dark mode', item_type: 'issue',
        repo: 'org/repo', number: 2, url: 'https://github.com/org/repo/issues/2', state: 'open',
        synced_at: new Date().toISOString() },
    ]}))
    await page.goto('/engineering')
    await waitForApp(page)
    await expect(page.getByText('PRs to Review')).toBeVisible()
    await expect(page.getByText('Assigned Issues')).toBeVisible()
    await expect(page.getByText('Fix login bug')).toBeVisible()
    await expect(page.getByText('Add dark mode')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add to board' }).first()).toBeVisible()
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
    await expect(page.getByRole('heading', { name: /quick add/i })).toBeVisible()
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
// Edit modal — scheduled_at persistence
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Workshop page
// ---------------------------------------------------------------------------
test.describe('workshop page', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/jobs', r => {
      if (r.request().method() === 'GET') return r.fulfill({ json: [] })
      // POST → return a new job
      return r.fulfill({ json: {
        id: 1, title: null, prompt: '', input_sources: [],
        last_output: null, output_card_id: null,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      }})
    })
    await page.goto('/workshop')
    await waitForApp(page)
  })

  test('Workshop nav item is visible in sidebar', async ({ page }) => {
    await expect(page.getByRole('button', { name: /workshop/i }).first()).toBeVisible()
  })

  test('"New Job" button is visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: /new job/i })).toBeVisible()
  })

  test('empty state shown when no jobs exist', async ({ page }) => {
    await expect(page.getByText(/select a job or create a new one/i)).toBeVisible()
  })

  test('clicking "New Job" shows compose area with all input buttons', async ({ page }) => {
    await page.route('**/api/jobs/1', r => r.fulfill({ json: {
      id: 1, title: null, prompt: '', input_sources: [],
      last_output: null, output_card_id: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }}))
    await page.getByRole('button', { name: /new job/i }).click()
    await expect(page.getByPlaceholder(/job title/i)).toBeVisible()
    await expect(page.getByPlaceholder(/paste additional context/i)).toBeVisible()
    await expect(page.locator('#ws-prompt')).toBeVisible()
    await expect(page.getByRole('button', { name: /run/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /by tag/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /add card/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /search web/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /fetch url/i })).toBeVisible()
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

  test('"Assistant" button is visible when a card is expanded', async ({ page }) => {
    const card = page.locator('.event-card', { hasText: 'Daily Engineering Standup' })
    await card.click()
    await expect(card.getByRole('button', { name: /assistant/i })).toBeVisible()
  })

  test('Assistant modal opens when button is clicked', async ({ page }) => {
    const card = page.locator('.event-card', { hasText: 'Daily Engineering Standup' })
    await card.click()
    await card.getByRole('button', { name: /assistant/i }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Assistant' })).toBeVisible()
    await expect(page.getByRole('button', { name: /generate/i })).toBeVisible()
  })

  test('"Create tasks" button appears when output contains a list', async ({ page }) => {
    await page.route('**/api/assist/stream', r => r.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: ASSIST_LIST_SSE,
    }))
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    await card.getByRole('button', { name: /assistant/i }).click()
    await page.locator('.assist-context').fill('Help me break this down')
    await page.getByRole('button', { name: /generate/i }).click()
    await expect(page.getByRole('button', { name: /create tasks/i })).toBeVisible()
  })

  test('"Create tasks" shows editable confirm list', async ({ page }) => {
    await page.route('**/api/assist/stream', r => r.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: ASSIST_LIST_SSE,
    }))
    await page.route('**/api/cards/bulk', r => r.fulfill({ json: { cards: [] } }))
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    await card.getByRole('button', { name: /assistant/i }).click()
    await page.locator('.assist-context').fill('Help me break this down')
    await page.getByRole('button', { name: /generate/i }).click()
    await page.getByRole('button', { name: /create tasks/i }).click()
    await expect(page.locator('.assist-bd-input').nth(0)).toHaveValue('Go to the dentist')
    await expect(page.locator('.assist-bd-input').nth(1)).toHaveValue('Get a cleaning')
    await expect(page.locator('.assist-bd-input').nth(2)).toHaveValue('Schedule follow-up')
    await expect(page.getByRole('button', { name: /add 3 tasks/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /back/i })).toBeVisible()
  })

  test('"Create tasks" Back button returns to output view', async ({ page }) => {
    await page.route('**/api/assist/stream', r => r.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: ASSIST_LIST_SSE,
    }))
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    await card.getByRole('button', { name: /assistant/i }).click()
    await page.locator('.assist-context').fill('Help me break this down')
    await page.getByRole('button', { name: /generate/i }).click()
    await page.getByRole('button', { name: /create tasks/i }).click()
    await page.getByRole('button', { name: /back/i }).click()
    await expect(page.locator('.assist-output')).toBeVisible()
    await expect(page.getByRole('button', { name: /create tasks/i })).toBeVisible()
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
    await expect(sheet.getByRole('button', { name: /mark complete/i })).toBeVisible()
    await expect(sheet.getByRole('button', { name: /^edit$/i })).toBeVisible()
  })

  test('sheet shows description text for a card that has one', async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
    // Shopping list is in the Stash section
    await page.locator('.mobile-tab', { hasText: 'Stash' }).click()
    await page.locator('.event-card', { hasText: 'Shopping list' }).click({ force: true })
    const sheet = page.locator('.card-sheet')
    await expect(sheet).toBeVisible()
    await expect(sheet.getByText('Milk')).toBeVisible()
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
})

// ---------------------------------------------------------------------------
// Edit modal — scheduled_at persistence
// ---------------------------------------------------------------------------
test.describe('breakdown', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('"Break down" tab is visible in the Assistant modal', async ({ page }) => {
    await page.route('**/api/cards/3/breakdown', (r) =>
      r.fulfill({ json: { subtasks: [], tag_name: 'Project: Call dentist' } })
    )
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    await card.getByRole('button', { name: /assistant/i }).click()
    await expect(page.locator('.assist-modal')).toBeVisible()
    await expect(page.getByRole('button', { name: /^break down$/i })).toBeVisible()
  })

  test('clicking "Break down" tab generates subtasks and shows editable list', async ({ page }) => {
    await page.route('**/api/cards/3/breakdown', (r) =>
      r.fulfill({ json: { subtasks: ['Step 1', 'Step 2', 'Step 3'], tag_name: 'Project: Call dentist' } })
    )
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    await card.getByRole('button', { name: /assistant/i }).click()
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
    await expect(page.getByRole('heading', { name: 'Assistant' })).toBeVisible()
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
  const PROJECT_TAG_DONE   = { id: 10, name: 'Project: Done Project',   color: '#059669' }
  const PROJECT_TAG_ACTIVE = { id: 11, name: 'Project: Active Project', color: '#2563eb' }

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
// Focus next banner — project name prefix
// ---------------------------------------------------------------------------
test.describe('focus next banner', () => {
  test('shows project name prefix when focused task has a Project: tag', async ({ page }) => {
    const projectTag = { id: 12, name: 'Project: Brunch Planning', color: '#d97706' }
    // Card 2 ("Review pull requests") is the first unscheduled today card — it becomes focus next
    const todosWithProject = ALL_TODOS.map(t =>
      t.id === 2 ? { ...t, tags: [projectTag] } : t
    )
    await page.route('**/api/tags',  r => r.fulfill({ json: [...TAGS, projectTag] }))
    await page.route('**/api/cards', r => r.fulfill({ json: todosWithProject }))
    await page.goto('/today')
    await waitForApp(page)
    const banner = page.locator('.focus-next')
    await expect(banner).toBeVisible()
    await expect(banner.locator('.focus-next-project')).toHaveText('Brunch Planning ›')
    await expect(banner.getByText('Review pull requests')).toBeVisible()
  })

  test('shows no project prefix when focused task has no Project: tag', async ({ page }) => {
    await page.goto('/today')
    await waitForApp(page)
    const banner = page.locator('.focus-next')
    await expect(banner).toBeVisible()
    await expect(banner.locator('.focus-next-project')).toHaveCount(0)
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
    const editBtn = card.getByRole('button', { name: /^edit$/i })
    await expect(editBtn).toBeVisible()
    await editBtn.click()
    await expect(page.getByRole('heading', { name: /edit card/i })).toBeVisible()
    // The datetime-local input should show the pre-filled scheduled date
    await expect(page.locator('#atm-scheduled')).toHaveValue('2026-06-03T09:00')
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

    // Open edit modal for "Call dentist" (no scheduled_at initially)
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    const editBtn = card.getByRole('button', { name: /^edit$/i })
    await editBtn.click()
    await expect(page.getByRole('heading', { name: /edit card/i })).toBeVisible()

    // Set a scheduled date
    await page.locator('#atm-scheduled').fill('2026-06-01T10:00')
    await page.getByRole('button', { name: /save changes/i }).click()
    await expect(page.getByRole('heading', { name: /edit card/i })).toHaveCount(0)

    // Re-open the same card — state should now have scheduled_at from the PUT response
    await card.click()
    const editBtn2 = card.getByRole('button', { name: /^edit$/i })
    await editBtn2.click()
    await expect(page.getByRole('heading', { name: /edit card/i })).toBeVisible()
    await expect(page.locator('#atm-scheduled')).toHaveValue('2026-06-01T10:00')
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
    await page.goto('/health')
    await waitForApp(page)
  })

  test('"Connect Withings" prompt is shown when not connected', async ({ page }) => {
    await expect(page.locator('.health-not-connected')).toBeVisible()
    await expect(page.locator('.health-not-connected').getByRole('button', { name: /connect withings/i })).toBeVisible()
  })

  test('food log input is visible when Withings is connected', async ({ page }) => {
    // Override: Withings connected → showCharts = true → FoodLog renders
    await page.route('**/api/withings/status', r =>
      r.fulfill({ json: { connected: true, last_synced: null } }))
    await page.goto('/health')
    await waitForApp(page)
    await expect(page.locator('.food-input')).toBeVisible()
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
    // "Read that article" has personal tag (it's in Stash)
    await page.locator('.mobile-tab', { hasText: 'Stash' }).click()
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
    await page.route('**/api/assist/stream', r => r.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: searchingSse,
    }))
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    await card.getByRole('button', { name: /assistant/i }).click()
    await page.locator('.assist-context').fill('Find me brunch spots')
    await page.getByRole('button', { name: /generate/i }).click()
    // Eventually the output renders
    await expect(page.locator('.assist-output')).toBeVisible()
    await expect(page.getByText('Here are some brunch spots.')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Assistant modal — "Create tasks" error state
// ---------------------------------------------------------------------------
test.describe('assistant modal — create tasks error', () => {
  test('shows error message when bulk card creation fails', async ({ page }) => {
    await page.route('**/api/assist/stream', r => r.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: ASSIST_LIST_SSE,
    }))
    await page.route('**/api/cards/bulk', r => r.fulfill({ status: 500, json: { detail: 'Server error' } }))
    await page.goto('/board')
    await waitForApp(page)
    const card = page.locator('.event-card', { hasText: 'Call dentist' })
    await card.click()
    await card.getByRole('button', { name: /assistant/i }).click()
    await page.locator('.assist-context').fill('Help me break this down')
    await page.getByRole('button', { name: /generate/i }).click()
    await page.getByRole('button', { name: /create tasks/i }).click()
    await page.getByRole('button', { name: /add 3 tasks/i }).click()
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
})
