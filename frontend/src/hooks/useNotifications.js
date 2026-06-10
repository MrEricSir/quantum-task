import { useState, useEffect, useRef } from 'react'

const LEAD_MINUTES = 15
const POLL_MS = 60_000
const STORAGE_KEY = 'qt_shown_notifs'
const ENABLED_KEY = 'qt_notifs_enabled'
const CHANNEL_NAME = 'qt_notifications'

function loadShown() {
  try { return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')) }
  catch { return new Set() }
}

function saveShown(set) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify([...set])) }
  catch {}
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData = atob(base64)
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)))
}

async function registerPushSubscription() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return
  try {
    const reg = await navigator.serviceWorker.ready
    // Re-use existing subscription if present (idempotent)
    let sub = await reg.pushManager.getSubscription()
    if (!sub) {
      const keyRes = await fetch('/api/push/vapid-key')
      if (!keyRes.ok) return
      const { public_key } = await keyRes.json()
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(public_key),
      })
    }
    // Always re-POST so the server record stays current after a DB wipe
    await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    })
  } catch {
    // Push not supported or blocked — silent fallback to in-page notifications
  }
}

export function useNotifications(todos, onOpenTodo) {
  const [permission, setPermission] = useState(
    () => (typeof Notification !== 'undefined' ? Notification.permission : 'denied')
  )
  const [enabled, setEnabledState] = useState(
    () => localStorage.getItem(ENABLED_KEY) !== 'false'
  )
  const shownRef = useRef(loadShown())
  const channelRef = useRef(null)

  const setEnabled = (val) => {
    setEnabledState(val)
    localStorage.setItem(ENABLED_KEY, String(val))
  }

  // Handle notification clicks from BroadcastChannel (in-page) and SW messages
  useEffect(() => {
    // BroadcastChannel — in-page notifications
    if (typeof BroadcastChannel !== 'undefined') {
      const ch = new BroadcastChannel(CHANNEL_NAME)
      channelRef.current = ch
      ch.onmessage = (e) => {
        if (e.data?.type === 'open_todo' && e.data.todoId) {
          window.focus()
          onOpenTodo?.(e.data.todoId)
        }
      }
    }
    // Service worker messages — push notifications
    const swHandler = (e) => {
      if (e.data?.type === 'open_todo' && e.data.todoId) {
        window.focus()
        onOpenTodo?.(e.data.todoId)
      }
    }
    navigator.serviceWorker?.addEventListener('message', swHandler)

    return () => {
      channelRef.current?.close()
      navigator.serviceWorker?.removeEventListener('message', swHandler)
    }
  }, [onOpenTodo])

  // Subscribe to Web Push when permission is granted
  useEffect(() => {
    if (permission === 'granted') registerPushSubscription()
  }, [permission])

  const requestPermission = async () => {
    if (typeof Notification === 'undefined') return
    const result = await Notification.requestPermission()
    setPermission(result)
  }

  // In-page notification polling (fires when app is open)
  useEffect(() => {
    if (permission !== 'granted' || !enabled) return

    const check = () => {
      const now = Date.now()
      const cutoff = now + LEAD_MINUTES * 60_000

      todos.forEach((todo) => {
        if (!todo.scheduled_at || todo.completed) return
        const t = new Date(todo.scheduled_at).getTime()
        if (t < now || t > cutoff) return

        const key = `${todo.id}:${todo.scheduled_at}`
        if (shownRef.current.has(key)) return
        shownRef.current.add(key)
        saveShown(shownRef.current)

        const mins = Math.round((t - now) / 60_000)
        const notif = new Notification(todo.title, {
          body: mins <= 1 ? 'Due now' : `Due in ${mins} minute${mins !== 1 ? 's' : ''}`,
          icon: '/icon.svg',
          tag: key,
        })
        notif.onclick = () => {
          window.focus()
          if (channelRef.current) {
            channelRef.current.postMessage({ type: 'open_todo', todoId: todo.id })
          }
        }
      })
    }

    check()
    const id = setInterval(check, POLL_MS)
    return () => clearInterval(id)
  }, [permission, enabled, todos])

  return { permission, enabled, setEnabled, requestPermission }
}
