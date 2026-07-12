const BASE = '/api/cards'
const TAGS_BASE = '/api/tags'

// Send the browser's local date on every request so the server uses the
// user's clock for section assignment, habit resets, and event filtering
// rather than the server clock (which is UTC on Cloud Run).
function localDate() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// Returns a local datetime string without timezone suffix, e.g. "2026-06-15T23:00:00".
// Use this instead of new Date().toISOString() when the value will be stored as a
// naive datetime and filtered/displayed by local date — e.g. food log consumed_at.
export function localDateTime() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function apiFetch(url, opts = {}) {
  const headers = {
    'X-Local-Date': localDate(),
    'X-UTC-Offset': String(new Date().getTimezoneOffset()),
    ...opts.headers,
  }
  return fetch(url, { ...opts, headers })
}

export async function fetchWeather(lat, lon) {
  const res = await apiFetch(`/api/briefing/weather?lat=${lat}&lon=${lon}`)
  if (!res.ok) return null
  return res.json()
}

export async function fetchTags() {
  const res = await apiFetch(TAGS_BASE)
  if (!res.ok) throw new Error('Failed to fetch tags')
  return res.json()
}

export async function createTag(data) {
  const res = await apiFetch(TAGS_BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create tag')
  return res.json()
}

export async function updateTag(id, data) {
  const res = await apiFetch(`${TAGS_BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(res.status === 409 ? 'Tag name already exists.' : 'Failed to update tag')
  return res.json()
}

export async function replaceTag(fromId, toId) {
  const res = await apiFetch(`${TAGS_BASE}/${fromId}/replace`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_tag_id: toId }),
  })
  if (!res.ok) throw new Error('Failed to replace tag')
}

export async function deleteTag(id) {
  const res = await apiFetch(`${TAGS_BASE}/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete tag')
}

export async function addTagToCard(cardId, tagId) {
  const res = await apiFetch(`${BASE}/${cardId}/tags/${tagId}`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to add tag')
}

export async function removeTagFromCard(cardId, tagId) {
  const res = await apiFetch(`${BASE}/${cardId}/tags/${tagId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to remove tag')
}

export async function fetchCards() {
  const res = await apiFetch(BASE)
  if (!res.ok) throw new Error('Failed to fetch cards')
  return res.json()
}

export async function searchCards(q) {
  const res = await apiFetch(`${BASE}/search?q=${encodeURIComponent(q)}`)
  if (!res.ok) throw new Error('Failed to search')
  return res.json()
}

export async function createCard(data) {
  const res = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create card')
  return res.json()
}

export async function updateCard(id, data) {
  const res = await apiFetch(`${BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update card')
  return res.json()
}

export async function deleteCard(id) {
  const res = await apiFetch(`${BASE}/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete card')
}

export async function archiveCard(id) {
  return updateCard(id, { archived: true })
}

export async function unarchiveCard(id) {
  return updateCard(id, { archived: false })
}

export async function parseCard(text) {
  const res = await apiFetch(`${BASE}/parse`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to parse')
  }
  return res.json()
}

export async function parseBulkCards(text) {
  const res = await apiFetch(`${BASE}/parse-bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to parse')
  }
  return res.json()
}

export async function bulkCreateCards(cards) {
  const res = await apiFetch(`${BASE}/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cards }),
  })
  if (!res.ok) throw new Error('Failed to create cards')
  return res.json()
}

export async function breakdownCard(id) {
  const res = await apiFetch(`${BASE}/${id}/breakdown`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to generate breakdown')
  return res.json()
}

export async function commitBreakdown(id, subtasks, tag_name) {
  const res = await apiFetch(`${BASE}/${id}/breakdown/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subtasks, tag_name }),
  })
  if (!res.ok) throw new Error('Failed to commit breakdown')
  return res.json()
}

export async function reorderCards(updates) {
  // updates: [{ id, section, position }, ...]
  const res = await apiFetch(`${BASE}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  if (!res.ok) throw new Error('Failed to reorder cards')
}

export async function fetchCalendarMappings() {
  const res = await apiFetch('/api/calendar-mappings')
  if (!res.ok) throw new Error('Failed to fetch calendar mappings')
  return res.json()
}

export async function saveCalendarMappings(mappings) {
  const res = await apiFetch('/api/calendar-mappings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mappings),
  })
  if (!res.ok) throw new Error('Failed to save calendar mappings')
  return res.json()
}

export async function fetchCalendarEvents({ force = false } = {}) {
  const res = await apiFetch(`/api/calendar-events${force ? '?force=1' : ''}`)
  if (!res.ok) throw new Error('Failed to fetch calendar events')
  return res.json()
}

export async function fetchDiscoveryFeeds() {
  const res = await apiFetch('/api/discovery/feeds')
  if (!res.ok) throw new Error('Failed to fetch discovery feeds')
  return res.json()
}

export async function saveDiscoveryFeeds(feeds) {
  const res = await apiFetch('/api/discovery/feeds', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(feeds),
  })
  if (!res.ok) throw new Error('Failed to save discovery feeds')
  return res.json()
}

export async function fetchDiscoveryInterests() {
  const res = await apiFetch('/api/discovery/interests')
  if (!res.ok) throw new Error('Failed to fetch discovery interests')
  return res.json()
}

export async function saveDiscoveryInterests(interests) {
  const res = await apiFetch('/api/discovery/interests', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ interests }),
  })
  if (!res.ok) throw new Error('Failed to save discovery interests')
  return res.json()
}

export async function fetchDiscoveryEvents() {
  const res = await apiFetch('/api/discovery/events')
  if (!res.ok) throw new Error('Failed to fetch discovery events')
  return res.json()
}

export async function testDiscoveryFeeds() {
  const res = await apiFetch('/api/discovery/test-feeds')
  if (!res.ok) throw new Error('Failed to test feeds')
  return res.json()
}

export async function fetchDiscoveryFeedback() {
  const res = await apiFetch('/api/discovery/feedback')
  if (!res.ok) throw new Error('Failed to fetch discovery feedback')
  return res.json()
}

export async function saveDiscoveryFeedback(eventUid, eventTitle, eventDescription, interested) {
  const res = await apiFetch('/api/discovery/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event_uid: eventUid, event_title: eventTitle, event_description: eventDescription, interested }),
  })
  if (!res.ok) throw new Error('Failed to save feedback')
  return res.json()
}

export async function fetchExportToken() {
  const res = await apiFetch('/api/settings/export-token')
  if (!res.ok) throw new Error('Failed to fetch export token')
  return res.json().then((d) => d.token)
}

export async function rotateExportToken() {
  const res = await apiFetch('/api/settings/export-token/rotate', { method: 'POST' })
  if (!res.ok) throw new Error('Failed to rotate export token')
  return res.json().then((d) => d.token)
}

export async function fetchHabits() {
  const res = await apiFetch('/api/habits')
  if (!res.ok) throw new Error('Failed to fetch habits')
  return res.json()
}

export async function fetchArchivedHabits() {
  const res = await apiFetch('/api/habits?archived=true')
  if (!res.ok) throw new Error('Failed to fetch archived habits')
  return res.json()
}

export async function archiveHabit(id) {
  return updateHabit(id, { archived: true })
}

export async function unarchiveHabit(id) {
  return updateHabit(id, { archived: false })
}

export async function createHabit(data) {
  const res = await apiFetch('/api/habits', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create habit')
  return res.json()
}

export async function updateHabit(id, data) {
  const res = await apiFetch(`/api/habits/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update habit')
  return res.json()
}

export async function deleteHabit(id) {
  const res = await apiFetch(`/api/habits/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete habit')
}

export async function checkHabit(id) {
  const res = await apiFetch(`/api/habits/${id}/check`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to check habit')
}

export async function uncheckHabit(id) {
  const res = await apiFetch(`/api/habits/${id}/check`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to uncheck habit')
}

export async function fetchEngineeringConfig() {
  const res = await apiFetch('/api/engineering/config')
  if (!res.ok) throw new Error('Failed to fetch engineering config')
  return res.json()
}

export async function saveEngineeringConfig(data) {
  const res = await apiFetch('/api/engineering/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to save engineering config')
  return res.json()
}

export async function syncEngineering() {
  const res = await apiFetch('/api/engineering/sync', { method: 'POST' })
  if (!res.ok) throw new Error('Failed to sync engineering items')
  return res.json()
}

export async function fetchEngineeringItems() {
  const res = await apiFetch('/api/engineering/items')
  if (!res.ok) throw new Error('Failed to fetch engineering items')
  return res.json()
}

export async function fetchJobs() {
  const res = await apiFetch('/api/jobs')
  if (!res.ok) throw new Error('Failed to fetch jobs')
  return res.json()
}

export async function createJob(data) {
  const res = await apiFetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create job')
  return res.json()
}

export async function updateJob(id, data) {
  const res = await apiFetch(`/api/jobs/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update job')
  return res.json()
}

export async function deleteJob(id) {
  const res = await apiFetch(`/api/jobs/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete job')
}

// ── Withings ──────────────────────────────────────────────────────────────────

export async function fetchWithingsStatus() {
  const res = await apiFetch('/api/withings/status')
  if (!res.ok) throw new Error('Failed to fetch Withings status')
  return res.json()
}

export async function fetchWithingsAuthUrl() {
  const res = await apiFetch('/api/withings/auth-url')
  if (!res.ok) throw new Error('Failed to get Withings auth URL')
  return res.json()
}

export async function syncWithings() {
  const res = await apiFetch('/api/withings/sync', { method: 'POST' })
  if (!res.ok) throw new Error('Failed to sync Withings')
  return res.json()
}

export async function disconnectWithings() {
  const res = await apiFetch('/api/withings/disconnect', { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to disconnect Withings')
}

export async function fetchWithingsHealthData(days = 90) {
  const res = await apiFetch(`/api/withings/health-data?days=${days}`)
  if (!res.ok) throw new Error('Failed to fetch Withings health data')
  return res.json()
}

export async function fetchWithingsGoals() {
  const res = await apiFetch('/api/withings/goals')
  if (!res.ok) throw new Error('Failed to fetch Withings goals')
  return res.json()
}

export async function saveWithingsGoals(goals) {
  const res = await apiFetch('/api/withings/goals', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(goals),
  })
  if (!res.ok) throw new Error('Failed to save Withings goals')
  return res.json()
}

export async function fetchInsights() {
  const res = await apiFetch('/api/insights')
  if (!res.ok) throw new Error('Failed to fetch insights')
  return res.json()
}

export async function fetchHealthCorrelations() {
  const res = await apiFetch('/api/health/correlations')
  if (!res.ok) throw new Error('Failed to fetch correlations')
  return res.json()
}

export async function fetchHealthExperiment() {
  const res = await apiFetch('/api/health/experiment')
  if (!res.ok) throw new Error('Failed to fetch experiment')
  return res.json()
}

export async function dismissHealthExperiment() {
  const res = await apiFetch('/api/health/experiment', { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to dismiss experiment')
  return res.json()
}

export async function fetchHealthExperiments() {
  const res = await apiFetch('/api/health/experiments')
  if (!res.ok) throw new Error('Failed to fetch experiment history')
  return res.json()
}

export async function createFoodEntry(data) {
  const res = await apiFetch('/api/food', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to log food entry')
  return res.json()
}

export async function fetchFoodEntries(date) {
  const url = date ? `/api/food?date_str=${date}` : '/api/food'
  const res = await apiFetch(url)
  if (!res.ok) throw new Error('Failed to fetch food log')
  return res.json()
}

export async function deleteFoodEntry(id) {
  const res = await apiFetch(`/api/food/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete food entry')
}

export async function fetchTelegramConfig() {
  const res = await apiFetch('/api/telegram/config')
  if (!res.ok) throw new Error('Failed to fetch Telegram config')
  return res.json()
}

export async function saveTelegramConfig(config) {
  const res = await apiFetch('/api/telegram/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error('Failed to save Telegram config')
  return res.json()
}

export async function testTelegramConfig() {
  const res = await apiFetch('/api/telegram/test', { method: 'POST' })
  if (!res.ok) throw new Error(`Server error ${res.status}`)
  return res.json()
}

export async function checkAuth() {
  const res = await fetch('/api/auth/check')
  // 401 = auth enabled, not logged in. Any other failure = backend down, let it throw.
  if (res.status === 401) return { authed: false, enabled: true }
  if (!res.ok) throw new Error(`Auth check failed: ${res.status}`)
  return res.json()
}

export async function login(password) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  if (!res.ok) throw new Error('Wrong password')
}

export async function logout() {
  await fetch('/api/auth/logout', { method: 'POST' })
}

// ── Card threads (multi-turn assistant conversations) ─────────────────────────

export async function fetchCardThread(cardId) {
  const res = await apiFetch(`/api/cards/${cardId}/thread`)
  if (!res.ok) throw new Error('Failed to fetch thread')
  return res.json()
}

// Returns the raw Response for SSE streaming — caller handles the reader
export function sendThreadMessage(cardId, content) {
  return apiFetch(`/api/cards/${cardId}/thread/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export async function saveThreadOutput(cardId, output) {
  const res = await apiFetch(`/api/cards/${cardId}/thread/output`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ output }),
  })
  if (!res.ok) throw new Error('Failed to save output')
  return res.json()
}

export async function updateThreadContext(cardId, context) {
  const res = await apiFetch(`/api/cards/${cardId}/thread/context`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context }),
  })
  if (!res.ok) throw new Error('Failed to update context')
}

export async function clearCardThread(cardId) {
  const res = await apiFetch(`/api/cards/${cardId}/thread`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to clear thread')
}
