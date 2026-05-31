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

  // Listen for notification clicks forwarded via BroadcastChannel
  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return
    const ch = new BroadcastChannel(CHANNEL_NAME)
    channelRef.current = ch
    ch.onmessage = (e) => {
      if (e.data?.type === 'open_todo' && e.data.todoId) {
        window.focus()
        onOpenTodo?.(e.data.todoId)
      }
    }
    return () => ch.close()
  }, [onOpenTodo])

  const requestPermission = async () => {
    if (typeof Notification === 'undefined') return
    const result = await Notification.requestPermission()
    setPermission(result)
  }

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
          // BroadcastChannel lets the click handler (which runs in the page context)
          // communicate back to the React app even if the tab was backgrounded
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
