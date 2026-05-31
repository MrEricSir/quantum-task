const BASE = '/api/todos'
const TAGS_BASE = '/api/tags'

export async function fetchTags() {
  const res = await fetch(TAGS_BASE)
  if (!res.ok) throw new Error('Failed to fetch tags')
  return res.json()
}

export async function createTag(data) {
  const res = await fetch(TAGS_BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create tag')
  return res.json()
}

export async function updateTag(id, data) {
  const res = await fetch(`${TAGS_BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(res.status === 409 ? 'Tag name already exists.' : 'Failed to update tag')
  return res.json()
}

export async function replaceTag(fromId, toId) {
  const res = await fetch(`${TAGS_BASE}/${fromId}/replace`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_tag_id: toId }),
  })
  if (!res.ok) throw new Error('Failed to replace tag')
}

export async function deleteTag(id) {
  const res = await fetch(`${TAGS_BASE}/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete tag')
}

export async function addTagToTodo(todoId, tagId) {
  const res = await fetch(`${BASE}/${todoId}/tags/${tagId}`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to add tag')
}

export async function removeTagFromTodo(todoId, tagId) {
  const res = await fetch(`${BASE}/${todoId}/tags/${tagId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to remove tag')
}

export async function fetchTodos() {
  const res = await fetch(BASE)
  if (!res.ok) throw new Error('Failed to fetch todos')
  return res.json()
}

export async function searchTodos(q) {
  const res = await fetch(`${BASE}/search?q=${encodeURIComponent(q)}`)
  if (!res.ok) throw new Error('Failed to search')
  return res.json()
}

export async function createTodo(data) {
  const res = await fetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create todo')
  return res.json()
}

export async function updateTodo(id, data) {
  const res = await fetch(`${BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update todo')
  return res.json()
}

export async function deleteTodo(id) {
  const res = await fetch(`${BASE}/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete todo')
}

export async function parseTodo(text) {
  const res = await fetch(`${BASE}/parse`, {
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
  const res = await fetch('/api/calendar-mappings')
  if (!res.ok) throw new Error('Failed to fetch calendar mappings')
  return res.json()
}

export async function saveCalendarMappings(mappings) {
  const res = await fetch('/api/calendar-mappings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mappings),
  })
  if (!res.ok) throw new Error('Failed to save calendar mappings')
  return res.json()
}

export async function fetchCalendarEvents() {
  const res = await fetch('/api/calendar-events')
  if (!res.ok) throw new Error('Failed to fetch calendar events')
  return res.json()
}

export async function fetchExportToken() {
  const res = await fetch('/api/settings/export-token')
  if (!res.ok) throw new Error('Failed to fetch export token')
  return res.json().then((d) => d.token)
}

export async function rotateExportToken() {
  const res = await fetch('/api/settings/export-token/rotate', { method: 'POST' })
  if (!res.ok) throw new Error('Failed to rotate export token')
  return res.json().then((d) => d.token)
}

export async function fetchHabits() {
  const res = await fetch('/api/habits')
  if (!res.ok) throw new Error('Failed to fetch habits')
  return res.json()
}

export async function createHabit(data) {
  const res = await fetch('/api/habits', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create habit')
  return res.json()
}

export async function updateHabit(id, data) {
  const res = await fetch(`/api/habits/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update habit')
  return res.json()
}

export async function deleteHabit(id) {
  const res = await fetch(`/api/habits/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete habit')
}

export async function checkHabit(id) {
  const res = await fetch(`/api/habits/${id}/check`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to check habit')
}

export async function uncheckHabit(id) {
  const res = await fetch(`/api/habits/${id}/check`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to uncheck habit')
}

export async function fetchNotes() {
  const res = await fetch('/api/notes')
  if (!res.ok) throw new Error('Failed to fetch notes')
  return res.json()
}

export async function createNote(data) {
  const res = await fetch('/api/notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create note')
  return res.json()
}

export async function updateNote(id, data) {
  const res = await fetch(`/api/notes/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update note')
  return res.json()
}

export async function deleteNote(id) {
  const res = await fetch(`/api/notes/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete note')
}

export async function promoteNote(id) {
  const res = await fetch(`/api/notes/${id}/promote`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to promote note')
  return res.json()
}

export async function reorderTodos(updates) {
  // updates: [{ id, section, position }, ...]
  const res = await fetch(`${BASE}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  if (!res.ok) throw new Error('Failed to reorder todos')
}
