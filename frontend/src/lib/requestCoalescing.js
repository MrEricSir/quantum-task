// Pure in-flight-request coalescing, used by src/sw.js's fetch handler (and unit-tested here
// directly, since a service worker's own globals -- self, caches, FetchEvent -- can't run in
// this project's jsdom-based test environment). See sw.js for why this exists: a page reload
// cancels the browser's side of any in-flight request, but a backend thread that already
// picked the request up keeps running regardless -- repeated reloads during a cold start pile
// up duplicate copies of the same request on an already-backed-up single instance.

// The handful of idempotent GETs fired on every page load, worth coalescing across reloads.
export const COALESCE_PATHNAMES = new Set([
  '/api/auth/check',
  '/api/cards',
  '/api/tags',
  '/api/habits',
  '/api/calendar-events',
  '/api/settings/navigation',
])

export function shouldCoalesce(method, pathname) {
  return method === 'GET' && COALESCE_PATHNAMES.has(pathname)
}

// fetchFn is injected (rather than importing the global fetch) purely so tests can supply a
// controllable mock -- in production this is always the real fetch.
export function createCoalescer(fetchFn) {
  const inFlight = new Map()

  return function coalesce(request, key) {
    let pending = inFlight.get(key)
    if (!pending) {
      pending = fetchFn(request).finally(() => inFlight.delete(key))
      inFlight.set(key, pending)
    }
    return pending.then((res) => res.clone())
  }
}
