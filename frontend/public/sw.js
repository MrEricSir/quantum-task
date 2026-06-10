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
