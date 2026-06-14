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
  // For navigation requests (loading the SPA), serve the cached shell when offline
  if (event.request.mode === 'navigate') {
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
      data: { todoId: data.todoId },
    })
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const todoId = event.notification.data?.todoId
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if ('focus' in client) {
          if (todoId) client.postMessage({ type: 'open_todo', todoId })
          return client.focus()
        }
      }
      return clients.openWindow('/')
    })
  )
})
