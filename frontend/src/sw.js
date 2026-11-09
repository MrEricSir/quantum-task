// Custom service worker, bundled by vite-plugin-pwa's injectManifest strategy (see
// vite.config.js) rather than its default generateSW strategy -- generateSW would
// generate its own sw.js from scratch and overwrite this file's build output,
// silently dropping the push/notificationclick handlers below (a real incident: they
// shipped to dist/ for a while under generateSW with zero effect, since the backend's
// push notifications had no service-worker code left to receive them).
import { cleanupOutdatedCaches, precacheAndRoute } from 'workbox-precaching'
import { createCoalescer, shouldCoalesce } from './lib/requestCoalescing'

cleanupOutdatedCaches()
precacheAndRoute(self.__WB_MANIFEST)

const CACHE = 'qt-shell-v1'

self.addEventListener('install', (event) => {
  // Cache the app shell so the app loads when offline
  event.waitUntil(caches.open(CACHE).then((c) => c.add('/')))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  // Remove any old cache versions
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  )
  self.clients.claim()
})

// In-flight coalescing for the handful of idempotent GETs the app fires on every single
// load (auth check, cards/tags/habits/calendar, nav prefs) -- see lib/requestCoalescing.js
// for why. Matched on pathname only (ignoring query string) so an unrelated variant of the
// same endpoint -- e.g. /api/calendar-events?force=1's manual, deliberately-uncached refresh
// -- never collides with the plain bootstrap fetch; the in-flight map itself is still keyed
// on the full URL so the two variants track their own separate in-flight state rather than
// merging.
const _coalesce = createCoalescer(fetch)

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)

  if (shouldCoalesce(req.method, url.pathname)) {
    event.respondWith(_coalesce(req, url.pathname + url.search))
    return
  }

  // For navigation requests (loading the SPA), serve the cached shell when offline.
  // Exclude /api/ paths so the browser handles redirects natively (e.g. OAuth callbacks).
  if (req.mode === 'navigate') {
    if (url.pathname.startsWith('/api/')) return
    event.respondWith(
      fetch(req).catch(() => caches.match('/'))
    )
  }
  // Everything else: network only — no stale data risk
})

self.addEventListener('push', (event) => {
  const data = event.data?.json() ?? {}
  event.waitUntil(
    self.registration.showNotification(data.title ?? 'Quantum Task', {
      body: data.body ?? '',
      icon: '/icon.svg',
      badge: '/icon.svg',
      tag: data.tag,
      data: { cardId: data.cardId },
    })
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const cardId = event.notification.data?.cardId
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if ('focus' in client) {
          if (cardId) client.postMessage({ type: 'open_card', cardId })
          return client.focus()
        }
      }
      return clients.openWindow('/')
    })
  )
})
