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

function apiFetch(url, opts = {}) {
  const headers = { 'X-Local-Date': localDate(), ...opts.headers }
  return fetch(url, { ...opts, headers })
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

export async function fetchCalendarEvents() {
  const res = await apiFetch('/api/calendar-events')
  if (!res.ok) throw new Error('Failed to fetch calendar events')
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
