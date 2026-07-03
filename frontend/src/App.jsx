import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  DndContext,
  DragOverlay,
  closestCorners,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import { arrayMove } from '@dnd-kit/sortable'
import Column from './components/board/Column'
import TodoCard from './components/board/TodoCard'
import Archive from './components/board/Archive'
import Sidebar from './components/layout/Sidebar'
import MobileNav from './components/layout/MobileNav'
import TagFilterBar from './components/layout/TagFilterBar'
import AddTodoModal from './components/modals/AddTodoModal'
import CardSheet from './components/modals/CardSheet'
import QuickAddModal from './components/modals/QuickAddModal'
import SearchModal from './components/modals/SearchModal'
import KeyboardShortcutsModal from './components/modals/KeyboardShortcutsModal'
import TagManagerModal from './components/modals/TagManagerModal'
import CalendarSettings from './components/modals/CalendarSettings'
import GithubSettings from './components/modals/GithubSettings'
import TodayPage from './components/pages/TodayPage'
import HabitsPage from './components/pages/HabitsPage'
import CalendarPage from './components/pages/CalendarPage'
import EngineeringPage from './components/pages/EngineeringPage'
import WorkshopPage from './components/pages/WorkshopPage'
import HealthPage from './components/pages/HealthPage'
import LoginPage from './components/pages/LoginPage'
import WithingsSettings from './components/modals/WithingsSettings'
import QueueIndicator from './components/shared/QueueIndicator'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { GearIcon, MagnifyingGlassIcon } from '@radix-ui/react-icons'
import { useNotifications } from './hooks/useNotifications'
import { useCards } from './hooks/useCards'
import { useHabits } from './hooks/useHabits'
import { useCalendar } from './hooks/useCalendar'
import { useWithings } from './hooks/useWithings'
import {
  fetchTags,
  fetchCards,
  reorderCards,
  createTag, updateTag, deleteTag, replaceTag,
  parseCard,
  checkAuth, logout,
  syncEngineering, fetchEngineeringItems,
  createFoodEntry,
} from './api'
import './App.css'

export const SECTIONS = ['today', 'week', 'month', 'later']
export const SECTION_LABELS = {
  today: 'Today',
  week: 'This Week',
  month: 'This Month',
  later: 'Stash',
}
const SECTION_COLORS = {
  today: 'var(--color-today)',
  week: 'var(--color-week)',
  month: 'var(--color-month)',
  later: 'var(--color-later)',
}

export default function App() {
  const [isOnline, setIsOnline] = useState(() => navigator.onLine)
  const [authed, setAuthed] = useState(null)   // null=checking, true/false
  const [authEnabled, setAuthEnabled] = useState(false)
  const [tags, setTags] = useState([])
  const [tagsLoading, setTagsLoading] = useState(true)
  const [briefingKey, setBriefingKey] = useState(0)
  const invalidateBriefing = useCallback(() => setBriefingKey((k) => k + 1), [])
  const [showModal, setShowModal] = useState(false)
  const [showQuickAdd, setShowQuickAdd] = useState(false)
  const [quickAddInitialText, setQuickAddInitialText] = useState('')
  const [showSearch, setShowSearch] = useState(false)
  const [showTagManager, setShowTagManager] = useState(false)
  const [showCalendarSettings, setShowCalendarSettings] = useState(false)
  const [showGithubSettings, setShowGithubSettings] = useState(false)
  const [showWithingsSettings, setShowWithingsSettings] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [editingTodo, setEditingTodo] = useState(null)
  const [activeTodo, setActiveTodo] = useState(null)
  const [activeSection, setActiveSection] = useState('today')
  const [weather, setWeather] = useState(null)
  const [engineeringItems, setEngineeringItems] = useState([])
  const [lastEngineeringSynced, setLastEngineeringSynced] = useState(null)
  const [engineeringSyncing, setEngineeringSyncing] = useState(false)

  const {
    todos, setTodos, loading: cardsLoading, todosRef,
    handleAddTodo, handleUpdateTodo, handleDeleteTodo, handleToggle,
    handleAddTag, handleRemoveTag, handleAddCard, handleUpdateCard,
    handleDeleteCard, handleArchiveCard, handleUnarchiveCard,
  } = useCards({ authed, tags, invalidateBriefing })

  const {
    habits, archivedHabits,
    handleAddHabit, handleUpdateHabit, handleDeleteHabit,
    handleArchiveHabit, handleUnarchiveHabit, handleToggleHabit,
  } = useHabits({ authed, invalidateBriefing })

  const {
    calendarEvents, lastRefreshed, calendarRefreshing, handleRefreshCalendar,
  } = useCalendar({ authed, invalidateBriefing })

  const {
    status: withingsStatus,
    healthData,
    healthGoals,
    syncing: withinsSyncing,
    syncError: withinsSyncError,
    handleSync: handleWithingsSync,
    handleDisconnect: handleWithingsDisconnect,
    handleSaveGoals: handleSaveWithingsGoals,
    loadStatus: reloadWithingsStatus,
    loadHealthData: reloadWithingsHealthData,
  } = useWithings({ authed })

  const [isImperial, setIsImperial] = useState(() => localStorage.getItem('health-unit') === 'imperial')
  const toggleUnit = () => setIsImperial(v => {
    const next = !v
    localStorage.setItem('health-unit', next ? 'imperial' : 'metric')
    return next
  })

  // When a quick-add "steps goal" is detected, update or create a steps habit
  // rather than saving a standalone health goal (steps = streak-tracked habit only).
  const handleSaveStepGoal = async (stepGoal) => {
    const existing = habits.find(h => h.withings_metric === 'steps' && !h.archived)
    if (existing) {
      await handleUpdateHabit(existing.id, { withings_goal: stepGoal })
    } else {
      await handleAddHabit({ name: 'Daily Steps', withings_metric: 'steps', withings_goal: stepGoal, tag_ids: [] })
    }
  }

  const loading = cardsLoading || tagsLoading

  const { permission: notifPermission, enabled: notifEnabled, setEnabled: setNotifEnabled, requestPermission } = useNotifications(
    todos,
    (todoId) => {
      const todo = todosRef.current.find((t) => t.id === todoId)
      if (todo) openEdit(todo)
    }
  )

  const navigate = useNavigate()
  const location = useLocation()
  const isTodayPage       = location.pathname === '/today'       || location.pathname.startsWith('/today/tag/')
  const isHabitsPage      = location.pathname === '/habits'      || location.pathname.startsWith('/habits/tag/')
  const isBoardPage       = location.pathname === '/board'       || location.pathname.startsWith('/board/tag/')
  const isCalendarPage    = location.pathname === '/calendar'    || location.pathname.startsWith('/calendar/tag/')
  const isEngineeringPage = location.pathname === '/engineering'
  const isWorkshopPage    = location.pathname === '/workshop'
  const isHealthPage      = location.pathname === '/health'
  const currentPage       = isTodayPage ? 'today' : isHabitsPage ? 'habits' : isBoardPage ? 'board' : isCalendarPage ? 'calendar' : isEngineeringPage ? 'engineering' : isWorkshopPage ? 'workshop' : isHealthPage ? 'health' : 'today'

  const tagMatch =
    location.pathname.match(/^\/today\/tag\/(\d+)$/)    ||
    location.pathname.match(/^\/habits\/tag\/(\d+)$/)   ||
    location.pathname.match(/^\/board\/tag\/(\d+)$/)    ||
    location.pathname.match(/^\/calendar\/tag\/(\d+)$/)
  const selectedTagId = tagMatch ? parseInt(tagMatch[1]) : null
  const [isMobile, setIsMobile] = useState(() => window.matchMedia('(max-width: 640px)').matches)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 640px)')
    const handler = (e) => setIsMobile(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  useEffect(() => {
    const online = () => setIsOnline(true)
    const offline = () => setIsOnline(false)
    window.addEventListener('online', online)
    window.addEventListener('offline', offline)
    return () => { window.removeEventListener('online', online); window.removeEventListener('offline', offline) }
  }, [])

  const shortcutsRef = useRef([])
  shortcutsRef.current = [
    { key: 'n', label: 'New card',          group: 'action', action: ()  => setShowQuickAdd(true) },
    { key: '/', label: 'Search',            group: 'action', action: (e) => { e.preventDefault(); setShowSearch(true) } },
    { key: '?', label: 'Keyboard shortcuts',group: 'action', action: ()  => setShowShortcuts(true) },
    { key: 't', label: 'Today',             group: 'nav',    action: ()  => navigate('/today') },
    { key: 'b', label: 'Board',             group: 'nav',    action: ()  => navigate('/board') },
    { key: 'h', label: 'Habits',            group: 'nav',    action: ()  => navigate('/habits') },
    { key: 'c', label: 'Calendar',          group: 'nav',    action: ()  => navigate('/calendar') },
    { key: 'e', label: 'Engineering',       group: 'nav',    action: ()  => navigate('/engineering') },
    { key: 'w', label: 'Workshop',          group: 'nav',    action: ()  => navigate('/workshop') },
    { key: 'H', label: 'Health',            group: 'nav',    action: ()  => navigate('/health') },
  ]

  useEffect(() => {
    const handler = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const tag = document.activeElement?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (document.querySelector('[role="dialog"]')) return
      const shortcut = shortcutsRef.current.find((s) => s.key === e.key)
      if (shortcut) shortcut.action(e)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  const pendingToResume = useRef([])
  const [parseQueue, setParseQueue] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('parseQueue') ?? '[]')
      const queue = saved
        .filter((i) => i.status !== 'added')
        .map((i) =>
          // 'done' is from old confirm-flow code — treat as pending and re-submit
          i.status === 'done' ? { ...i, status: 'pending', errorMsg: '' } : i
        )
      // Collect items to re-submit after mount (can't call APIs during state init)
      pendingToResume.current = queue.filter((i) => i.status === 'pending')
      return queue
    } catch {
      return []
    }
  })

  useEffect(() => {
    localStorage.setItem('parseQueue', JSON.stringify(parseQueue))
  }, [parseQueue])

  const tagsRef = useRef(tags)
  tagsRef.current = tags

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: isMobile ? 999999 : 8 } })
  )

  useEffect(() => {
    checkAuth()
      .then(({ authed: a, enabled: e }) => {
        setAuthed(a)
        setAuthEnabled(e)
        if (location.pathname === '/') navigate('/today', { replace: true })
      })
      .catch(() => setAuthed(false))
  }, [])

  // Handle Withings OAuth callback redirect (?withings=connected|error lands in the new tab)
  useEffect(() => {
    if (!isBoardPage) return
    const params = new URLSearchParams(location.search)
    const result = params.get('withings')
    if (result === 'connected') {
      if (window.opener) {
        try {
          window.opener.postMessage({ type: 'withings-connected' }, window.location.origin)
        } catch {
          // opener may be inaccessible; fall through
        }
        window.close()
      } else {
        // No opener (cross-origin nav cleared it) — sync then reload in this tab
        handleWithingsSync()
      }
      navigate('/board', { replace: true })
    } else if (result === 'error') {
      const msg = params.get('msg') || 'Unknown error'
      if (window.opener) {
        try {
          window.opener.postMessage({ type: 'withings-error', msg }, window.location.origin)
        } catch { /* ignore */ }
        window.close()
      } else {
        navigate('/board', { replace: true })
      }
    }
  }, [isBoardPage]) // eslint-disable-line react-hooks/exhaustive-deps

  // Handle Web Share Target: /share?text=...
  useEffect(() => {
    if (location.pathname !== '/share') return
    const params = new URLSearchParams(location.search)
    const shared = [params.get('text'), params.get('title'), params.get('url')].filter(Boolean).join(' ')
    navigate('/today', { replace: true })
    if (shared) {
      setQuickAddInitialText(shared)
      setShowQuickAdd(true)
    }
  }, [location.pathname]) // eslint-disable-line react-hooks/exhaustive-deps

  // Redirect legacy routes to their new locations
  useEffect(() => {
    if (location.pathname === '/tasks' || location.pathname.startsWith('/tasks/tag/')) {
      navigate(location.pathname.replace('/tasks', '/board'), { replace: true })
    } else if (location.pathname === '/cards' || location.pathname.startsWith('/cards/tag/')) {
      navigate(location.pathname.replace('/cards', '/board'), { replace: true })
    } else if (location.pathname === '/overview' || location.pathname.startsWith('/tag/')) {
      navigate('/board', { replace: true })
    }
  }, [location.pathname]) // eslint-disable-line react-hooks/exhaustive-deps

  // Remove the HTML splash screen once auth check resolves
  useEffect(() => {
    if (authed === null) return
    const splash = document.getElementById('qt-splash')
    if (!splash) return
    splash.classList.add('qt-splash--out')
    const t = setTimeout(() => splash.remove(), 350)
    return () => clearTimeout(t)
  }, [authed])

  // App badge: count of overdue tasks
  useEffect(() => {
    if (!('setAppBadge' in navigator)) return
    const overdue = todos.filter((t) => !t.completed && (t.overdue_days ?? 0) > 0).length
    if (overdue > 0) {
      navigator.setAppBadge(overdue).catch(() => {})
    } else {
      navigator.clearAppBadge().catch(() => {})
    }
  }, [todos])

  const refreshEngineeringItems = useCallback(() => {
    setEngineeringSyncing(true)
    syncEngineering()
      .catch(() => {})  // silently ignore if not configured
      .finally(() => {
        fetchEngineeringItems()
          .then((items) => { setEngineeringItems(items); setLastEngineeringSynced(new Date()) })
          .catch(() => {})
          .finally(() => setEngineeringSyncing(false))
      })
  }, [])

  // Sync engineering on login, then poll every 15 minutes
  useEffect(() => {
    if (!authed) return
    refreshEngineeringItems()
  }, [authed]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const id = setInterval(refreshEngineeringItems, 15 * 60 * 1000)
    return () => clearInterval(id)
  }, [refreshEngineeringItems])

  // Fetch tags on login
  useEffect(() => {
    if (!authed) return
    fetchTags()
      .then(setTags)
      .catch(() => {})
      .finally(() => setTagsLoading(false))
  }, [authed])

  const activeTodos = todos.filter((t) => !t.completed && !t.archived && t.section !== 'none')
  const completedTodos = todos.filter((t) => t.completed && !t.archived)

  const visibleTags = tags.filter((tag) => {
    if (!tag.name.startsWith('Project: ')) return true
    return todos.some((t) => !t.completed && !t.archived && (t.tags ?? []).some((tg) => tg.id === tag.id))
  })

  const visibleCalendarEvents = selectedTagId === null
    ? calendarEvents
    : calendarEvents.filter((e) => e.tag_id === selectedTagId)

  const visibleActiveTodos = selectedTagId === null
    ? activeTodos
    : activeTodos.filter((t) => (t.tags ?? []).some((tag) => tag.id === selectedTagId))

  const todosBySection = SECTIONS.reduce((acc, s) => {
    if (s === 'later') {
      // "Stash" column shows both 'later' and legacy 'none' (reference) cards
      acc[s] = todos
        .filter((t) => (t.section === 'later' || t.section === 'none') && !t.completed && !t.archived &&
          (selectedTagId === null || (t.tags ?? []).some((tag) => tag.id === selectedTagId)))
        .sort((a, b) => a.position - b.position)
    } else {
      acc[s] = visibleActiveTodos
        .filter((t) => t.section === s)
        .sort((a, b) => a.position - b.position)
    }
    return acc
  }, {})

  const handleDragStart = useCallback((event) => {
    const todo = todosRef.current.find((t) => t.id === event.active.id)
    setActiveTodo(todo ?? null)
  }, [])

  const handleDragOver = useCallback((event) => {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const current = todosRef.current
    const dragged = current.find((t) => t.id === active.id)
    if (!dragged) return

    // Don't do optimistic section changes for archive cards (completed)
    if (dragged.completed) return

    const activeSection = dragged.section
    // Treat 'none' and 'later' as the same section for drag purposes
    const normalizeSection = (s) => (s === 'none' ? 'later' : s)
    const overSection = SECTIONS.includes(String(over.id))
      ? String(over.id)
      : normalizeSection(current.find((t) => t.id === over.id)?.section)

    if (!overSection || normalizeSection(activeSection) === overSection) return

    setTodos((prev) =>
      prev.map((t) => (t.id === active.id ? { ...t, section: overSection } : t))
    )
  }, [])

  const handleDragEnd = useCallback((event) => {
    const { active, over } = event
    setActiveTodo(null)
    if (!over) return

    const current = todosRef.current
    const dragged = current.find((t) => t.id === active.id)
    if (!dragged) return

    const overCard = current.find((t) => t.id === over.id)

    // Archive drop: board card → archive (mark complete)
    if (!dragged.completed && (over.id === 'archive' || overCard?.completed === true)) {
      handleUpdateTodo(dragged.id, { completed: true })
      invalidateBriefing()
      return
    }

    // Archive drag-out: archive card → board section (mark incomplete)
    if (dragged.completed) {
      const overSection = SECTIONS.includes(String(over.id))
        ? String(over.id)
        : (overCard && !overCard.completed ? overCard.section : null)
      if (overSection) {
        handleUpdateTodo(dragged.id, { completed: false, section: overSection })
        invalidateBriefing()
      }
      return
    }

    // Normal board-to-board reordering
    const section = dragged.section
    const sectionTodos = current
      .filter((t) => (section === 'later' ? (t.section === 'later' || t.section === 'none') : t.section === section))
      .sort((a, b) => a.position - b.position)

    let reordered = sectionTodos

    // Reorder within section if dropped on a sibling card
    if (!SECTIONS.includes(String(over.id))) {
      if (overCard && (overCard.section === section || (section === 'later' && overCard.section === 'none'))) {
        const fromIdx = sectionTodos.findIndex((t) => t.id === active.id)
        const toIdx = sectionTodos.findIndex((t) => t.id === over.id)
        if (fromIdx !== -1 && toIdx !== -1 && fromIdx !== toIdx) {
          reordered = arrayMove(sectionTodos, fromIdx, toIdx)
        }
      }
    }

    const updatedSection = reordered.map((t, i) => ({ ...t, position: i }))
    const newTodos = [
      ...current.filter((t) => section === 'later'
        ? (t.section !== 'later' && t.section !== 'none')
        : t.section !== section),
      ...updatedSection,
    ]

    setTodos(newTodos)
    reorderCards(
      updatedSection.map(({ id, section, position }) => ({ id, section, position }))
    )
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreateTag = async (data) => {
    const created = await createTag(data)
    setTags((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)))
  }

  const handleUpdateTag = async (tagId, data) => {
    const updated = await updateTag(tagId, data)
    setTags((prev) => prev.map((t) => (t.id === tagId ? updated : t)).sort((a, b) => a.name.localeCompare(b.name)))
    setTodos((prev) =>
      prev.map((t) => ({
        ...t,
        tags: (t.tags ?? []).map((tg) => (tg.id === tagId ? updated : tg)),
      }))
    )
  }

  const handleDeleteTag = async (tagId) => {
    await deleteTag(tagId)
    setTags((prev) => prev.filter((t) => t.id !== tagId))
    setTodos((prev) =>
      prev.map((t) => ({ ...t, tags: (t.tags ?? []).filter((tg) => tg.id !== tagId) }))
    )
  }

  const handleReplaceTag = async (fromTagId, toTagId) => {
    await replaceTag(fromTagId, toTagId)
    const toTag = tags.find((t) => t.id === toTagId)
    setTags((prev) => prev.filter((t) => t.id !== fromTagId))
    setTodos((prev) =>
      prev.map((todo) => {
        const hasfrom = (todo.tags ?? []).some((tg) => tg.id === fromTagId)
        if (!hasfrom) return todo
        const hasTo = (todo.tags ?? []).some((tg) => tg.id === toTagId)
        const filtered = (todo.tags ?? []).filter((tg) => tg.id !== fromTagId)
        return { ...todo, tags: hasTo ? filtered : [...filtered, toTag] }
      })
    )
  }

  const processQueueItem = (id, text) => {
    parseCard(text)
      .then(async (result) => {
        const tagIds = (result.suggested_tags ?? [])
          .map((name) => tagsRef.current.find((t) => t.name.toLowerCase() === name.toLowerCase())?.id)
          .filter(Boolean)
        try {
          if (result.type === 'habit') {
            await handleAddHabit({ name: result.title, tag_ids: tagIds })
          } else {
            await handleAddTodo({
              title: result.title,
              description: text,
              section: result.section,
              scheduled_at: result.scheduled_at || null,
              recurrence_rule: result.recurrence_rule || null,
              tag_ids: tagIds,
            })
          }
          setParseQueue((prev) => prev.filter((i) => i.id !== id))
        } catch {
          setParseQueue((prev) =>
            prev.map((i) => i.id === id ? { ...i, status: 'error', errorMsg: 'Failed to add todo to board' } : i)
          )
        }
      })
      .catch((e) => {
        setParseQueue((prev) =>
          prev.map((i) => i.id === id ? { ...i, status: 'error', errorMsg: e.message } : i)
        )
      })
  }

  // Re-submit any items that were pending when the page last closed
  useEffect(() => {
    for (const item of pendingToResume.current) {
      processQueueItem(item.id, item.text)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const addToParseQueue = (text) => {
    const id = Date.now()
    setParseQueue((prev) => [...prev, { id, text, status: 'pending', result: null, errorMsg: '' }])
    processQueueItem(id, text)
  }

  const handleClearQueue = () => {
    setParseQueue((prev) => prev.filter((i) => i.status === 'pending'))
  }

  const handleMoveSection = async (todoId, newSection) => {
    await handleUpdateTodo(todoId, { section: newSection })
    setActiveSection(newSection)
  }

  const handlePageNavigate = (page, tagId) => {
    if (page === 'today')       return navigate(tagId ? `/today/tag/${tagId}` : '/today')
    if (page === 'engineering') return navigate('/engineering')
    if (page === 'workshop')    return navigate('/workshop')
    if (page === 'health')      return navigate('/health')
    if (page === 'habits')      return navigate(tagId ? `/habits/tag/${tagId}` : '/habits')
    return navigate(tagId ? `/${page}/tag/${tagId}` : `/${page}`)
  }

  const [defaultSection, setDefaultSection] = useState('today')
  const [showNewSheet, setShowNewSheet] = useState(false)

  const openEdit = (todo) => {
    setDefaultSection(todo?.section ?? 'today')
    setEditingTodo(todo)
    setShowModal(true)
  }

  const openNewCard = (section = 'today') => {
    setDefaultSection(section)
    if (window.matchMedia('(max-width: 640px)').matches) {
      setShowNewSheet(true)
    } else {
      setEditingTodo(null)
      setShowModal(true)
    }
  }

  const closeModal = () => {
    setShowModal(false)
    setEditingTodo(null)
  }

  const handleBreakdownCommit = ({ tag, cards, archived_card }) => {
    if (tag) {
      setTags((prev) => {
        if (prev.some((t) => t.id === tag.id)) return prev
        return [...prev, tag].sort((a, b) => a.name.localeCompare(b.name))
      })
    }
    setTodos((prev) => {
      const updated = archived_card
        ? prev.map((t) => (t.id === archived_card.id ? archived_card : t))
        : prev
      return [...updated, ...cards]
    })
  }

  const handleModalSave = async (data) => {
    if (editingTodo) {
      await handleUpdateTodo(editingTodo.id, data)
    } else {
      await handleAddTodo(data)
    }
    closeModal()
  }

  if (authed === null) return null
  if (!authed) return <LoginPage onLogin={() => setAuthed(true)} />

  return (
    <div className="app">
      <video className="app-bg-video" autoPlay muted loop playsInline disablePictureInPicture>
        <source src="/bg.webm" type="video/webm" />
      </video>
      {!isOnline && (
        <div className="offline-banner">No internet connection — changes won't sync until you're back online.</div>
      )}
      <header className="app-header">
        <div className="header-inner">
          <div className="header-title">
            <img src="/logo.svg" alt="Quantum Task" className="header-logo" />
            <h1>Quantum Task</h1>
          </div>
          {weather && (
            <div className="header-weather">
              <span className="header-weather-emoji">{weather.emojis}</span>
              <span className="header-weather-temp">{weather.high}°&thinsp;/&thinsp;{weather.low}°</span>
            </div>
          )}
          <div className="header-actions">
            <button
              className="btn-ghost"
              onClick={() => setShowSearch(true)}
              title="Search (press /)"
              aria-label="Search"
              style={{ fontSize: 18, padding: '7px 10px' }}
            >
              <MagnifyingGlassIcon width={18} height={18} />
            </button>
            <QueueIndicator
              items={parseQueue}
              onDismiss={(id) => setParseQueue((prev) => prev.filter((i) => i.id !== id))}
              onRetry={(id, text) => {
                setParseQueue((prev) => prev.map((i) => i.id === id ? { ...i, status: 'pending', errorMsg: '' } : i))
                processQueueItem(id, text)
              }}
              onClearErrors={() => setParseQueue((prev) => prev.filter((i) => i.status === 'pending'))}
            />
            <button className="btn-primary" onClick={() => setShowQuickAdd(true)}>
              + Add
            </button>
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button className="btn-ghost" title="Settings" aria-label="Settings">
                  <GearIcon width={22} height={22} />
                </button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content className="settings-dropdown" align="end" sideOffset={6}>
                  <DropdownMenu.Item className="settings-dropdown-item" onSelect={() => setShowCalendarSettings(true)}>
                    &#128197; Calendar
                  </DropdownMenu.Item>
                  <DropdownMenu.Item className="settings-dropdown-item" onSelect={() => setShowGithubSettings(true)}>
                    &#128279; Engineering (GitHub)
                  </DropdownMenu.Item>
                  <DropdownMenu.Item className="settings-dropdown-item" onSelect={() => setShowWithingsSettings(true)}>
                    &#10084;&#65039; Withings{withingsStatus?.connected ? '' : ' (not connected)'}
                  </DropdownMenu.Item>
                  <DropdownMenu.Item
                    className="settings-dropdown-item settings-dropdown-notif"
                    onSelect={(e) => { e.preventDefault(); toggleUnit() }}
                  >
                    <span>&#9878;&#65039; Units: {isImperial ? 'lbs' : 'kg'}</span>
                    <span className={`notif-toggle ${isImperial ? 'notif-toggle--on' : ''}`} />
                  </DropdownMenu.Item>
                  <DropdownMenu.Item className="settings-dropdown-item" onSelect={() => setShowTagManager(true)}>
                    &#127991; Tags
                  </DropdownMenu.Item>
                  <DropdownMenu.Item
                    className="settings-dropdown-item settings-dropdown-notif"
                    disabled={typeof Notification === 'undefined' || notifPermission === 'denied'}
                    onSelect={(e) => {
                      e.preventDefault()
                      if (notifPermission === 'default') {
                        requestPermission()
                      } else if (notifPermission === 'granted') {
                        setNotifEnabled(!notifEnabled)
                      }
                    }}
                  >
                    <span>&#128276; Notifications</span>
                    <span className={`notif-toggle ${notifPermission === 'granted' && notifEnabled ? 'notif-toggle--on' : ''}`} />
                  </DropdownMenu.Item>
                  <DropdownMenu.Item className="settings-dropdown-item" onSelect={() => setShowShortcuts(true)}>
                    &#9000; Keyboard shortcuts
                  </DropdownMenu.Item>
                  <DropdownMenu.Separator className="settings-dropdown-divider" />
                  <DropdownMenu.Item
                    className="settings-dropdown-item"
                    disabled={!authEnabled}
                    onSelect={async () => { if (authEnabled) { await logout(); setAuthed(false) } }}
                    style={!authEnabled ? { opacity: 0.4, cursor: 'default' } : undefined}
                    title={!authEnabled ? 'Auth is disabled in local dev' : undefined}
                  >
                    &#x1F512; Sign out
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          </div>
        </div>
      </header>

      <div className="app-body">
        <Sidebar
          tags={visibleTags}
          selectedTagId={selectedTagId}
          page={currentPage}
          onNavigate={handlePageNavigate}
        />

      <main className="board-wrapper">
        <TagFilterBar
          tags={visibleTags}
          selectedTagId={selectedTagId}
          page={currentPage}
          onNavigate={handlePageNavigate}
        />
        {loading ? (
          <div className="loading">Loading...</div>
        ) : isTodayPage ? (
          <TodayPage
            todos={selectedTagId ? todos.filter((t) => (t.tags ?? []).some((tg) => tg.id === selectedTagId)) : todos}
            calendarEvents={visibleCalendarEvents}
            habits={selectedTagId ? habits.filter((h) => (h.tags ?? []).some((tg) => tg.id === selectedTagId)) : habits}
            onToggle={handleToggle}
            onToggleHabit={handleToggleHabit}
            onEdit={openEdit}
            onSave={handleUpdateTodo}
            onDelete={handleDeleteTodo}
            onArchive={handleArchiveCard}
            onMove={handleMoveSection}
            allTags={tags}
            onBreakdown={handleBreakdownCommit}
            onWeather={setWeather}
            briefingKey={briefingKey}
            healthData={healthData}
            healthGoals={healthGoals}
            isImperial={isImperial}
          />
        ) : isHabitsPage ? (
          <HabitsPage
            habits={habits}
            archivedHabits={archivedHabits}
            allTags={tags}
            selectedTagId={selectedTagId}
            onToggle={handleToggleHabit}
            onAdd={handleAddHabit}
            onUpdate={handleUpdateHabit}
            onDelete={handleDeleteHabit}
            onArchive={handleArchiveHabit}
            onUnarchive={handleUnarchiveHabit}
            isImperial={isImperial}
          />
        ) : isBoardPage ? (
          <>
            <div className="mobile-tabs">
              {SECTIONS.map((s) => (
                <button
                  key={s}
                  className={`mobile-tab ${activeSection === s ? 'mobile-tab--active' : ''}`}
                  style={activeSection === s ? { borderBottomColor: SECTION_COLORS[s], color: SECTION_COLORS[s] } : {}}
                  onClick={() => setActiveSection(s)}
                >
                  {SECTION_LABELS[s]}
                  <span className="mobile-tab-count">{todosBySection[s].length}</span>
                </button>
              ))}
            </div>

            <DndContext
              sensors={sensors}
              collisionDetection={closestCorners}
              onDragStart={handleDragStart}
              onDragOver={handleDragOver}
              onDragEnd={handleDragEnd}
            >
              <div className="board">
                {SECTIONS.map((section) => (
                  <Column
                    key={section}
                    section={section}
                    label={SECTION_LABELS[section]}
                    todos={todosBySection[section]}
                    isActive={section === activeSection}
                    isMobile={isMobile}
                    onEdit={openEdit}
                    onSave={handleUpdateTodo}
                    onDelete={handleDeleteTodo}
                    onArchive={handleArchiveCard}
                    onToggle={handleToggle}
                    onMove={handleMoveSection}
                    onAdd={() => openNewCard(section)}
                    allTags={tags}
                    onBreakdown={handleBreakdownCommit}
                  />
                ))}
              </div>
              <DragOverlay dropAnimation={null}>
                {activeTodo ? <TodoCard todo={activeTodo} isOverlay /> : null}
              </DragOverlay>
              <Archive
                todos={completedTodos}
                onEdit={openEdit}
                onDelete={handleDeleteTodo}
                onToggle={handleToggle}
              />
            </DndContext>
          </>
        ) : isCalendarPage ? (
          <CalendarPage
            events={visibleCalendarEvents}
            todos={visibleActiveTodos}
            onToggle={handleToggle}
            onEdit={openEdit}
            onRefresh={handleRefreshCalendar}
            lastRefreshed={lastRefreshed}
            refreshing={calendarRefreshing}
          />
        ) : isHealthPage ? (
          <HealthPage
            habits={habits}
            healthData={healthData}
            healthGoals={healthGoals}
            withingsConnected={withingsStatus?.connected ?? false}
            onOpenSettings={() => setShowWithingsSettings(true)}
            isImperial={isImperial}
            onToggleUnit={toggleUnit}
          />
        ) : isWorkshopPage ? (
          <WorkshopPage
            todos={todos.filter(t => !t.archived && !t.completed)}
            tags={tags}
            onAddCard={handleAddCard}
          />
        ) : isEngineeringPage ? (
          <EngineeringPage
            items={engineeringItems}
            todos={todos}
            lastSynced={lastEngineeringSynced}
            syncing={engineeringSyncing}
            onSync={refreshEngineeringItems}
            onOpenSettings={() => setShowGithubSettings(true)}
            onAddToBoard={async (item) => {
              await handleAddTodo({
                title: item.item_type === 'pr'
                  ? `GitHub PR: ${item.title}`
                  : `GitHub Issue: ${item.title}`,
                description: item.url,
                section: item.item_type === 'pr' ? 'today' : 'week',
                tag_ids: [],
                external_id: item.external_id,
              })
            }}
          />
        ) : null}
      </main>
      </div>{/* app-body */}

      <MobileNav
        page={currentPage}
        onNavigate={handlePageNavigate}
      />

      {showModal && (
        <AddTodoModal
          card={editingTodo}
          defaultSection={defaultSection}
          allTags={tags}
          onClose={closeModal}
          onSave={handleModalSave}
          onDelete={editingTodo ? async () => { await handleDeleteTodo(editingTodo.id); closeModal() } : undefined}
          onArchive={editingTodo ? async () => { await handleArchiveCard(editingTodo.id); closeModal() } : undefined}
        />
      )}

      {showNewSheet && (
        <CardSheet
          defaultSection={defaultSection}
          allTags={tags}
          onClose={() => setShowNewSheet(false)}
          onCreate={handleAddTodo}
        />
      )}

      {showCalendarSettings && (
        <CalendarSettings
          tags={tags}
          onClose={() => {
            setShowCalendarSettings(false)
            handleRefreshCalendar()
          }}
        />
      )}

      {showGithubSettings && (
        <GithubSettings
          onClose={() => setShowGithubSettings(false)}
          onSynced={() => fetchCards().then(setTodos).catch(() => {})}
        />
      )}

      {showWithingsSettings && (
        <WithingsSettings
          status={withingsStatus}
          syncing={withinsSyncing}
          healthGoals={healthGoals}
          onSync={handleWithingsSync}
          syncError={withinsSyncError}
          onDisconnect={handleWithingsDisconnect}
          onSaveGoals={handleSaveWithingsGoals}
          onClose={() => setShowWithingsSettings(false)}
          isImperial={isImperial}
        />
      )}

      {showTagManager && (
        <TagManagerModal
          tags={tags}
          onClose={() => setShowTagManager(false)}
          todos={todos}
          onCreate={handleCreateTag}
          onUpdate={handleUpdateTag}
          onDelete={handleDeleteTag}
          onReplace={handleReplaceTag}
        />
      )}

      {showSearch && (
        <SearchModal
          onClose={() => setShowSearch(false)}
          onEdit={(todo) => openEdit(todo)}
          habits={habits}
          onSelectHabit={() => { setShowSearch(false); navigate('/habits') }}
        />
      )}

      <KeyboardShortcutsModal
        open={showShortcuts}
        onClose={() => setShowShortcuts(false)}
        shortcuts={shortcutsRef.current}
      />

      {showQuickAdd && (
        <QuickAddModal
          allTags={tags}
          onClose={() => { setShowQuickAdd(false); setQuickAddInitialText('') }}
          onSaveTask={async (data) => { await handleAddTodo(data) }}
          onSaveHabit={async (data) => { await handleAddHabit(data) }}
          onSaveGoals={handleSaveWithingsGoals}
          onSaveStepGoal={handleSaveStepGoal}
          onSaveFood={createFoodEntry}
          isImperial={isImperial}
          initialText={quickAddInitialText}
        />
      )}

    </div>
  )
}
