/**
 * Matching logic for the quick-add completion flow (habit check-off / task complete).
 */

export function scoreMatch(name, query) {
  const n = name.toLowerCase(), q = query.toLowerCase()
  if (n === q) return 3
  if (n.includes(q) || q.includes(n)) return 1
  return 0
}

/**
 * Returns { kind: 'habit'|'task', id } for the best match across habits + tasks,
 * or null if nothing scores above zero.
 *
 * Excludes:
 *   - archived habits
 *   - automatic habits (withings_metric set)
 *   - completed tasks
 *   - archived tasks
 */
export function findBestMatch(title, habits, cards) {
  if (!title) return null
  const manualHabits = (habits ?? []).filter((h) => !h.archived && !h.withings_metric)
  const activeTasks = (cards ?? []).filter((c) => !c.completed && !c.archived)
  let best = null, bestScore = 0
  for (const h of manualHabits) {
    const s = scoreMatch(h.name, title)
    if (s > bestScore) { bestScore = s; best = { kind: 'habit', id: h.id } }
  }
  for (const c of activeTasks) {
    const s = scoreMatch(c.title, title)
    if (s > bestScore) { bestScore = s; best = { kind: 'task', id: c.id } }
  }
  return best
}
