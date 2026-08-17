/**
 * Matching logic for the quick-add completion flow (habit check-off / task complete).
 *
 * Same exact -> substring -> word-overlap strategy as the backend's
 * _fuzzy_match (telegram/bot.py) -- ported rather than shared, since Python
 * and JS can't literally share code. Tiers are magnitude-separated (1000 /
 * 500 / raw word-overlap count) rather than sequential filtering, because
 * findBestMatch below scans a single flat pool of habits+tasks for one max
 * score instead of narrowing tier by tier the way the backend does.
 */
export function scoreMatch(name, query) {
  const n = name.toLowerCase(), q = query.toLowerCase()
  if (n === q) return 1000
  if (n.includes(q) || q.includes(n)) return 500
  const qWords = q.split(/\s+/).filter((w) => w.length > 2)
  const nWords = new Set(n.split(/\s+/))
  const overlap = qWords.filter((w) => nWords.has(w)).length
  return overlap
}

/**
 * Returns { kind: 'habit'|'task', id } for the best match across habits + tasks,
 * or null if nothing scores above zero.
 *
 * Excludes:
 *   - archived habits
 *   - automatic habits (health_metric set)
 *   - completed tasks
 *   - archived tasks
 */
export function findBestMatch(title, habits, cards) {
  if (!title) return null
  const manualHabits = (habits ?? []).filter((h) => !h.archived && !h.health_metric)
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
