const BASE = '/api/todos'
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

export async function addTagToTodo(todoId, tagId) {
  const res = await apiFetch(`${BASE}/${todoId}/tags/${tagId}`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to add tag')
}

export async function removeTagFromTodo(todoId, tagId) {
  const res = await apiFetch(`${BASE}/${todoId}/tags/${tagId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to remove tag')
}

export async function fetchTodos() {
  const res = await apiFetch(BASE)
  if (!res.ok) throw new Error('Failed to fetch todos')
  return res.json()
}

export async function searchTodos(q) {
  const res = await apiFetch(`${BASE}/search?q=${encodeURIComponent(q)}`)
  if (!res.ok) throw new Error('Failed to search')
  return res.json()
}

export async function createTodo(data) {
  const res = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create todo')
  return res.json()
}

export async function updateTodo(id, data) {
  const res = await apiFetch(`${BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update todo')
  return res.json()
}

export async function deleteTodo(id) {
  const res = await apiFetch(`${BASE}/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete todo')
}

export async function parseTodo(text) {
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

export async function parseBulkTodos(text) {
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

export async function fetchNotes() {
  const res = await apiFetch('/api/notes')
  if (!res.ok) throw new Error('Failed to fetch notes')
  return res.json()
}

export async function fetchArchivedNotes() {
  const res = await apiFetch('/api/notes?archived=true')
  if (!res.ok) throw new Error('Failed to fetch archived notes')
  return res.json()
}

export async function archiveNote(id) {
  return updateNote(id, { archived: true })
}

export async function unarchiveNote(id) {
  return updateNote(id, { archived: false })
}

export async function createNote(data) {
  const res = await apiFetch('/api/notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create note')
  return res.json()
}

export async function updateNote(id, data) {
  const res = await apiFetch(`/api/notes/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update note')
  return res.json()
}

export async function deleteNote(id) {
  const res = await apiFetch(`/api/notes/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete note')
}

export async function promoteNote(id) {
  const res = await apiFetch(`/api/notes/${id}/promote`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to promote note')
  return res.json()
}

export async function reorderTodos(updates) {
  // updates: [{ id, section, position }, ...]
  const res = await apiFetch(`${BASE}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  if (!res.ok) throw new Error('Failed to reorder todos')
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
