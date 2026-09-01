const BASE = '/api/cards'
const TAGS_BASE = '/api/tags'

// Send the browser's local date on every request so the server uses the
// user's clock for section assignment, habit resets, and event filtering
// rather than the server clock (which is UTC on Cloud Run).
export function localDate() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// Returns YYYY-MM-DD for a Date object, using local time (not UTC).
export function localDateOf(d) {
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

export async function saveDiscoveryInterests(interests, maxDistanceMiles, distanceUnit) {
  const res = await apiFetch('/api/discovery/interests', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      interests,
      ...(maxDistanceMiles != null ? { max_distance_miles: maxDistanceMiles } : {}),
      ...(distanceUnit != null ? { distance_unit: distanceUnit } : {}),
    }),
  })
  if (!res.ok) throw new Error('Failed to save discovery interests')
  return res.json()
}

export async function fetchDiscoveryEvents({ force = false, lat = null, lon = null } = {}) {
  const params = new URLSearchParams()
  if (force) params.set('force', 'true')
  if (lat != null) params.set('lat', lat)
  if (lon != null) params.set('lon', lon)
  const qs = params.toString()
  const res = await apiFetch(`/api/discovery/events${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error('Failed to fetch discovery events')
  const events = await res.json()
  // While ranking runs in the background, the server returns events
  // immediately (stale or chronological) and flags them as such so the
  // caller can poll for the freshly-ranked list instead of blocking on it.
  const pending = res.headers.get('X-Ranking-Status') === 'pending'
  const distanceUnit = res.headers.get('X-Distance-Unit') || 'mi'
  return { events, pending, distanceUnit }
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

export async function fetchHabitStreakDays(id, from, to) {
  const params = new URLSearchParams({ from, to })
  const res = await apiFetch(`/api/habits/${id}/streak-days?${params}`)
  if (!res.ok) throw new Error('Failed to fetch habit streak history')
  return res.json()
}

export async function fetchTrip() {
  const res = await apiFetch('/api/trip')
  if (!res.ok) throw new Error('Failed to fetch trip')
  return res.json()
}

export async function startTrip(name, startDate) {
  const res = await apiFetch('/api/trip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name || null, start_date: startDate || null }),
  })
  if (!res.ok) throw new Error('Failed to start trip')
  return res.json()
}

export async function updateTrip(tripId, name, startDate) {
  const res = await apiFetch(`/api/trip/${tripId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name ?? null, start_date: startDate ?? null }),
  })
  if (!res.ok) throw new Error('Failed to update trip')
  return res.json()
}

export async function endTrip(tripId) {
  const res = await apiFetch(`/api/trip/${tripId}/end`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to end trip')
  return res.json()
}

export async function deleteTrip(tripId) {
  const res = await apiFetch(`/api/trip/${tripId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete trip')
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

export async function fetchStatusConfig() {
  const res = await apiFetch('/api/engineering/status-config')
  if (!res.ok) throw new Error('Failed to fetch status config')
  return res.json()
}

export async function saveStatusConfig(config) {
  const res = await apiFetch('/api/engineering/status-config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error('Failed to save status config')
  return res.json()
}

export async function fetchRepoTagsConfig() {
  const res = await apiFetch('/api/engineering/repo-tags')
  if (!res.ok) throw new Error('Failed to fetch repo tags config')
  return res.json()
}

export async function saveRepoTagsConfig(config) {
  const res = await apiFetch('/api/engineering/repo-tags', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error('Failed to save repo tags config')
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

export async function createHealthMeasurement(data) {
  const res = await apiFetch('/api/health/measurements', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to save measurement')
  return res.json()
}

export async function deleteHealthMeasurement(id) {
  const res = await apiFetch(`/api/health/measurements/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete measurement')
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

export async function updateFoodEntry(id, data) {
  const res = await apiFetch(`/api/food/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update food entry')
  return res.json()
}

export async function deleteFoodEntry(id) {
  const res = await apiFetch(`/api/food/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete food entry')
}

export async function fetchFoodQualityTrend(days = 30) {
  const res = await apiFetch(`/api/food/quality-trend?days=${days}`)
  if (!res.ok) return []
  return res.json()
}

export async function createWorkoutEntry(data) {
  const res = await apiFetch('/api/workouts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to log workout')
  return res.json()
}

export async function fetchWorkoutEntries(date) {
  const url = date ? `/api/workouts?date_str=${date}` : '/api/workouts'
  const res = await apiFetch(url)
  if (!res.ok) throw new Error('Failed to fetch workouts')
  return res.json()
}

export async function updateWorkoutEntry(id, data) {
  const res = await apiFetch(`/api/workouts/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update workout entry')
  return res.json()
}

export async function fetchWorkoutChart(start, end) {
  const params = new URLSearchParams()
  if (start) params.set('start', start)
  if (end)   params.set('end', end)
  const res = await apiFetch(`/api/workouts/chart?${params}`)
  if (!res.ok) throw new Error('Failed to fetch workout chart')
  return res.json()
}

export async function deleteWorkoutEntry(id) {
  const res = await apiFetch(`/api/workouts/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete workout entry')
}

export async function fetchMoodToday() {
  const res = await apiFetch('/api/mood/today')
  if (!res.ok) return null
  return res.json()
}

export async function logMood(energy, note = null) {
  const res = await apiFetch('/api/mood/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ energy, note }),
  })
  if (!res.ok) throw new Error('Failed to log mood')
  return res.json()
}

export async function fetchMoodRecent(days = 30) {
  const res = await apiFetch(`/api/mood/recent?days=${days}`)
  if (!res.ok) return []
  return res.json()
}

export async function fetchNavPreferences() {
  const res = await apiFetch('/api/settings/navigation')
  if (!res.ok) throw new Error('Failed to fetch navigation preferences')
  return res.json()
}

export async function saveNavPreferences(prefs) {
  const res = await apiFetch('/api/settings/navigation', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(prefs),
  })
  if (!res.ok) throw new Error('Failed to save navigation preferences')
  return res.json()
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

export async function testWeeklyReview() {
  const res = await apiFetch('/api/telegram/test-weekly-review', { method: 'POST' })
  if (!res.ok) throw new Error(`Server error ${res.status}`)
  return res.json()
}

export async function registerTelegramWebhook() {
  const res = await apiFetch('/api/telegram/register-webhook', { method: 'POST' })
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

export async function queueBridgeJob(cardId, branchName) {
  const body = { card_id: cardId }
  if (branchName) body.branch_name = branchName
  const res = await apiFetch('/api/bridge/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to queue bridge job')
  }
  return res.json()
}

export async function getBridgeJob(jobId) {
  const res = await apiFetch(`/api/bridge/jobs/${jobId}`)
  if (!res.ok) throw new Error('Failed to fetch bridge job')
  return res.json()
}

export async function queueResumeJob(jobId) {
  const res = await apiFetch(`/api/bridge/jobs/${jobId}/resume`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to queue resume job')
  }
  return res.json()
}

// Asks for an in-progress (or not-yet-started) job's branch to be renamed. For a running
// job this doesn't rename anything itself -- the bridge's heartbeat loop picks the
// request up and does the actual `git branch -m` locally (see bridge/router.py's
// request_job_rename docstring), so it takes effect on the next heartbeat, not instantly.
export async function requestBranchRename(jobId, branchName) {
  const res = await apiFetch(`/api/bridge/jobs/${jobId}/request-rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ branch_name: branchName }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to request branch rename')
  }
  return res.json()
}

export async function getBridgeJobChain(cardId) {
  const res = await apiFetch(`/api/bridge/jobs/card/${cardId}/chain`)
  if (!res.ok) throw new Error('Failed to fetch bridge job chain')
  return res.json()
}

// Latest job status per card with a bridge job -- for the Board/Today card tile's status
// badge (useBridgeJobStatuses), not the Code tab's own per-card chain fetch above.
export async function fetchBridgeJobStatuses() {
  const res = await apiFetch('/api/bridge/jobs/status')
  if (!res.ok) throw new Error('Failed to fetch bridge job statuses')
  const { statuses } = await res.json()
  return statuses
}

// Every currently-relevant bridge job across all cards (active, or finished recently) --
// for the Engineering page's fleet-level dashboard (useBridgeJobsDashboard), not the
// per-card badge (fetchBridgeJobStatuses) or the Code tab's own single-card chain fetch.
export async function fetchBridgeJobsDashboard() {
  const res = await apiFetch('/api/bridge/jobs/dashboard')
  if (!res.ok) throw new Error('Failed to fetch bridge jobs dashboard')
  const { jobs } = await res.json()
  return jobs
}

export async function queueCompanionJob(cardId, targetRepo, dependsOnJobId) {
  const res = await apiFetch('/api/bridge/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ card_id: cardId, target_repo: targetRepo, depends_on_job_id: dependsOnJobId }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to queue companion job')
  }
  return res.json()
}

export async function getKnownBridgeRepos() {
  const res = await apiFetch('/api/bridge/repos')
  if (!res.ok) throw new Error('Failed to fetch known repos')
  return res.json().then((d) => d.repos)
}

export async function fetchBridgeInstallToken() {
  const res = await apiFetch('/api/bridge/install-token')
  if (!res.ok) throw new Error('Failed to fetch bridge install token')
  return res.json().then((d) => d.token)
}

export async function rotateBridgeInstallToken() {
  const res = await apiFetch('/api/bridge/install-token/rotate', { method: 'POST' })
  if (!res.ok) throw new Error('Failed to rotate bridge install token')
  return res.json().then((d) => d.token)
}

export async function generateSpec(cardId) {
  const res = await apiFetch(`/api/cards/${cardId}/spec/generate`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to generate spec')
  return res.json()
}

export async function refreshEngineeringItem(itemId) {
  const res = await apiFetch(`/api/engineering/${itemId}/refresh`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to refresh GitHub item')
  return res.json()
}

export async function dismissEngineeringComment(commentId, dismissed) {
  const res = await apiFetch(`/api/engineering/comments/${commentId}/dismiss`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dismissed }),
  })
  if (!res.ok) throw new Error('Failed to update comment')
  return res.json()
}

export async function queueFixJob(jobId, commentIds) {
  const res = await apiFetch(`/api/bridge/jobs/${jobId}/fix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment_ids: commentIds }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to queue fix job')
  }
  return res.json()
}

export async function fetchContextFrom(cardId, source, { section, tagId } = {}) {
  const res = await apiFetch(`/api/cards/${cardId}/thread/context-from`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, section: section || null, tag_id: tagId || null }),
  })
  if (!res.ok) throw new Error('Failed to fetch context')
  return res.json()
}

export async function extractCardActions(cardId) {
  const res = await apiFetch(`/api/cards/${cardId}/extract-actions`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to extract action items')
  }
  return res.json()
}

export async function fetchTagReport(tagId, mode, { period, start, end } = {}) {
  const params = new URLSearchParams({ tag_id: tagId, mode })
  if (period) params.set('period', period)
  if (start) params.set('start', start)
  if (end) params.set('end', end)
  const res = await apiFetch(`/api/reports/tag?${params.toString()}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to generate report')
  }
  return res.json()
}

export async function fetchTagReportPeriodCounts(tagId, mode) {
  const params = new URLSearchParams({ tag_id: tagId, mode })
  const res = await apiFetch(`/api/reports/tag/period-counts?${params.toString()}`)
  if (!res.ok) throw new Error('Failed to load period counts')
  const { counts } = await res.json()
  return counts
}

// Triggers a browser download of the user's data export. Goes through
// apiFetch (rather than a bare `window.location.href` navigation, which
// can't carry custom headers) so this stays consistent with every other
// API call here if the export endpoint ever needs them.
export async function downloadExport() {
  const res = await apiFetch('/api/export')
  if (!res.ok) throw new Error('Export failed')
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] || 'export.json'
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
