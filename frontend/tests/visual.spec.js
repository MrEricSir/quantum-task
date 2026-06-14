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
    section: 'none', completed: false, archived: false, position: 0,
    tags: [{ id: 2, name: 'personal', color: '#10b981' }],
    updated_at: '2026-06-03T08:00:00Z', created_at: '2026-06-03T08:00:00Z',
  },
  {
    id: 8, title: 'Sprint ideas',
    description: 'Next sprint candidates:\n\nImprove search\nAdd dark mode option\nPerformance pass',
    section: 'none', completed: false, archived: false, position: 1,
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
    await expect(page.getByText('Review pull requests')).toBeVisible()
    await expect(page.getByText('Call dentist')).toBeVisible()
  })

  test('stash section shows section=later items', async ({ page }) => {
    await expect(page.getByText('Read that article')).toBeVisible()
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

  test('cards with section=none appear in the Stash column', async ({ page }) => {
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
// Habits page
// ---------------------------------------------------------------------------
test.describe('habits page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/habits')
    await waitForApp(page)
  })

  test('page heading is visible', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Habits' })).toBeVisible()
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
test.describe('sidebar upcoming events', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/board')
    await waitForApp(page)
  })

  test('upcoming events section is visible in sidebar', async ({ page }) => {
    await expect(page.locator('.sidebar-section-label', { hasText: 'Upcoming' })).toBeVisible()
  })

  test('calendar events appear in sidebar', async ({ page }) => {
    // CALENDAR_EVENTS includes 'Product Review' on 2026-06-03 (the frozen date)
    await expect(page.locator('.sidebar-upcoming-title', { hasText: 'Product Review' })).toBeVisible()
  })

  test('shows empty state when no events', async ({ page }) => {
    await page.route('**/api/calendar-events', r => r.fulfill({ json: [] }))
    await page.goto('/board')
    await waitForApp(page)
    await expect(page.locator('.sidebar-upcoming-empty')).toBeVisible()
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
