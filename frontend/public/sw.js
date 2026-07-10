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

self.addEventListener('fetch', (event) => {
  // For navigation requests (loading the SPA), serve the cached shell when offline.
  // Exclude /api/ paths so the browser handles redirects natively (e.g. OAuth callbacks).
  if (event.request.mode === 'navigate') {
    const url = new URL(event.request.url)
    if (url.pathname.startsWith('/api/')) return
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/'))
    )
  }
  // API calls and static assets: network only — no stale data risk
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
