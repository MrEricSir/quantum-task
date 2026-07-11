import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
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
import Card from './components/board/Card'
import Archive from './components/board/Archive'
import Sidebar from './components/layout/Sidebar'
import MobileNav from './components/layout/MobileNav'
import TagFilterBar from './components/layout/TagFilterBar'
import CardModal from './components/modals/CardModal'
import CardSheet from './components/modals/CardSheet'
import QuickAddModal from './components/modals/QuickAddModal'
import SearchModal from './components/modals/SearchModal'
import KeyboardShortcutsModal from './components/modals/KeyboardShortcutsModal'
import TagManagerModal from './components/modals/TagManagerModal'
import CalendarSettings from './components/modals/CalendarSettings'
import GithubSettings from './components/modals/GithubSettings'
import TodayPage from './components/pages/TodayPage'
import CalendarPage from './components/pages/CalendarPage'
import EngineeringPage from './components/pages/EngineeringPage'
import WorkshopPage from './components/pages/WorkshopPage'
import HealthPage from './components/pages/HealthPage'
import LoginPage from './components/pages/LoginPage'
import WithingsSettings from './components/modals/WithingsSettings'
import TelegramSettings from './components/modals/TelegramSettings'
import QueueIndicator from './components/shared/QueueIndicator'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { GearIcon, MagnifyingGlassIcon } from '@radix-ui/react-icons'
import { useNotifications } from './hooks/useNotifications'
import { useCards } from './hooks/useCards'
import { useHabits } from './hooks/useHabits'
import { useCalendar } from './hooks/useCalendar'
import { useWithings } from './hooks/useWithings'
import { useEngineering } from './hooks/useEngineering'
import { useModals } from './hooks/useModals'
import { ModalContext } from './context/ModalContext'
import {
  fetchTags,
  fetchCards,
  fetchWeather,
  reorderCards,
  createTag,
  parseCard,
  checkAuth, logout,
  createFoodEntry,
} from './api'
import './App.css'
import { SECTIONS, SECTION_LABELS } from './lib/sections'

export { SECTIONS, SECTION_LABELS }
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
  const {
    showModal,
    showQuickAdd, setShowQuickAdd,
    quickAddInitialText, setQuickAddInitialText,
    showSearch, setShowSearch,
    showTagManager, setShowTagManager,
    showCalendarSettings, setShowCalendarSettings,
    showGithubSettings, setShowGithubSettings,
    showWithingsSettings, setShowWithingsSettings,
    showTelegramSettings, setShowTelegramSettings,
    showShortcuts, setShowShortcuts,
    editingCard,
    defaultSection, showNewSheet, setShowNewSheet,
    openEdit, openNewCard, closeModal,
  } = useModals()
  const [quickAddStep, setQuickAddStep] = useState('input')
  const [activeCard, setActiveCard] = useState(null)
  const [activeSection, setActiveSection] = useState('today')
  const [highlightCalendarEventId, setHighlightCalendarEventId] = useState(null)
  const [weather, setWeather] = useState(() => {
    try {
      const cached = sessionStorage.getItem('weather')
      return cached ? JSON.parse(cached) : null
    } catch { return null }
  })
  const handleSetWeather = (w) => {
    setWeather(w)
    try { sessionStorage.setItem('weather', JSON.stringify(w)) } catch {}
  }
  const {
    cards, setCards, loading: cardsLoading, cardsRef,
    handleAddCard, handleUpdateCard, handleDeleteCard, handleToggle,
    handleAddTag, handleRemoveTag,
    handleArchiveCard, handleUnarchiveCard,
    handleUpdateTag, handleDeleteTag, handleReplaceTag,
  } = useCards({ authed, tags, setTags, invalidateBriefing })

  const {
    habits, archivedHabits,
    handleAddHabit, handleUpdateHabit, handleDeleteHabit,
    handleArchiveHabit, handleUnarchiveHabit, handleToggleHabit,
  } = useHabits({ authed, invalidateBriefing })

  const {
    calendarEvents, calendarLoading, lastRefreshed, calendarRefreshing, handleRefreshCalendar,
  } = useCalendar({ authed, invalidateBriefing })

  const { engineeringItems, lastEngineeringSynced, engineeringSyncing, refreshEngineeringItems } = useEngineering({ authed })

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
    cards,
    (cardId) => {
      const todo = cardsRef.current.find((t) => t.id === cardId)
      if (todo) openEdit(todo)
    }
  )

  const navigate = useNavigate()
  const location = useLocation()
  const isTodayPage       = location.pathname === '/today'       || location.pathname.startsWith('/today/tag/')
  const isBoardPage       = location.pathname === '/board'       || location.pathname.startsWith('/board/tag/')
  const isCalendarPage    = location.pathname === '/calendar'    || location.pathname.startsWith('/calendar/tag/')
  const isEngineeringPage = location.pathname === '/engineering'
  const isWorkshopPage    = location.pathname === '/workshop'
  // /habits is a legacy URL — treat it as the health page
  const isHealthPage      = location.pathname === '/health' || location.pathname === '/habits' || location.pathname.startsWith('/habits/')
  const currentPage       = isTodayPage ? 'today' : isBoardPage ? 'board' : isCalendarPage ? 'calendar' : isEngineeringPage ? 'engineering' : isWorkshopPage ? 'workshop' : isHealthPage ? 'health' : 'today'

  const tagMatch =
    location.pathname.match(/^\/today\/tag\/(\d+)$/)    ||
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
    if (!navigator.geolocation) return
    navigator.geolocation.getCurrentPosition(async ({ coords }) => {
      const w = await fetchWeather(coords.latitude, coords.longitude)
      if (w && w.emojis) handleSetWeather(w)
    }, () => {/* permission denied — weather stays as cached/SSE value */})
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
    { key: 'n', label: 'Capture',           group: 'action', action: ()  => { setQuickAddStep('input'); setShowQuickAdd(true) } },
    { key: 'a', label: 'Assist',            group: 'action', action: ()  => { setQuickAddStep('assist'); setShowQuickAdd(true) } },
    { key: '/', label: 'Search',            group: 'action', action: (e) => { e.preventDefault(); setShowSearch(true) } },
    { key: '?', label: 'Keyboard shortcuts',group: 'action', action: ()  => setShowShortcuts(true) },
    { key: 't', label: 'Today',             group: 'nav',    action: ()  => navigate('/today') },
    { key: 'b', label: 'Board',             group: 'nav',    action: ()  => navigate('/board') },
    { key: 'h', label: 'Health & Habits',   group: 'nav',    action: ()  => navigate('/health') },
    { key: 'c', label: 'Calendar',          group: 'nav',    action: ()  => navigate('/calendar') },
    { key: 'e', label: 'Engineering',       group: 'nav',    action: ()  => navigate('/engineering') },
    { key: 'w', label: 'Workshop',          group: 'nav',    action: ()  => navigate('/workshop') },
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
    const overdue = cards.filter((t) => !t.completed && (t.overdue_days ?? 0) > 0).length
    if (overdue > 0) {
      navigator.setAppBadge(overdue).catch(() => {})
    } else {
      navigator.clearAppBadge().catch(() => {})
    }
  }, [cards])

  // Fetch tags on login
  useEffect(() => {
    if (!authed) return
    fetchTags()
      .then(setTags)
      .catch(() => {})
      .finally(() => setTagsLoading(false))
  }, [authed])

  const activeCards = cards.filter((t) => !t.completed && !t.archived)
  const completedCards = cards.filter((t) => t.completed && !t.archived)

  const visibleTags = useMemo(() =>
    tags.filter((tag) => {
      if (!tag.name.startsWith('Project: ')) return true
      return cards.some((t) => !t.completed && !t.archived && (t.tags ?? []).some((tg) => tg.id === tag.id))
    }),
    [tags, cards]
  )

  const visibleCalendarEvents = selectedTagId === null
    ? calendarEvents
    : calendarEvents.filter((e) => e.tag_id === selectedTagId)

  const visibleActiveCards = selectedTagId === null
    ? activeCards
    : activeCards.filter((t) => (t.tags ?? []).some((tag) => tag.id === selectedTagId))

  const cardsBySection = SECTIONS.reduce((acc, s) => {
    acc[s] = visibleActiveCards
      .filter((t) => t.section === s)
      .sort((a, b) => a.position - b.position)
    return acc
  }, {})

  const handleDragStart = useCallback((event) => {
    const todo = cardsRef.current.find((t) => t.id === event.active.id)
    setActiveCard(todo ?? null)
  }, [])

  const handleDragOver = useCallback((event) => {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const current = cardsRef.current
    const dragged = current.find((t) => t.id === active.id)
    if (!dragged) return

    // Don't do optimistic section changes for archive cards (completed)
    if (dragged.completed) return

    const activeSection = dragged.section
    const overSection = SECTIONS.includes(String(over.id))
      ? String(over.id)
      : current.find((t) => t.id === over.id)?.section

    if (!overSection || activeSection === overSection) return

    setCards((prev) =>
      prev.map((t) => (t.id === active.id ? { ...t, section: overSection } : t))
    )
  }, [])

  const handleDragEnd = useCallback((event) => {
    const { active, over } = event
    setActiveCard(null)
    if (!over) return

    const current = cardsRef.current
    const dragged = current.find((t) => t.id === active.id)
    if (!dragged) return

    const overCard = current.find((t) => t.id === over.id)

    // Archive drop: board card → archive (mark complete)
    if (!dragged.completed && (over.id === 'archive' || overCard?.completed === true)) {
      handleUpdateCard(dragged.id, { completed: true })
      invalidateBriefing()
      return
    }

    // Archive drag-out: archive card → board section (mark incomplete)
    if (dragged.completed) {
      const overSection = SECTIONS.includes(String(over.id))
        ? String(over.id)
        : (overCard && !overCard.completed ? overCard.section : null)
      if (overSection) {
        handleUpdateCard(dragged.id, { completed: false, section: overSection })
        invalidateBriefing()
      }
      return
    }

    // Normal board-to-board reordering
    const section = dragged.section
    const sectionTodos = current
      .filter((t) => t.section === section)
      .sort((a, b) => a.position - b.position)

    let reordered = sectionTodos

    // Reorder within section if dropped on a sibling card
    if (!SECTIONS.includes(String(over.id))) {
      if (overCard && overCard.section === section) {
        const fromIdx = sectionTodos.findIndex((t) => t.id === active.id)
        const toIdx = sectionTodos.findIndex((t) => t.id === over.id)
        if (fromIdx !== -1 && toIdx !== -1 && fromIdx !== toIdx) {
          reordered = arrayMove(sectionTodos, fromIdx, toIdx)
        }
      }
    }

    const updatedSection = reordered.map((t, i) => ({ ...t, position: i }))
    const newCards = [
      ...current.filter((t) => t.section !== section),
      ...updatedSection,
    ]

    setCards(newCards)
    reorderCards(
      updatedSection.map(({ id, section, position }) => ({ id, section, position }))
    )
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreateTag = async (data) => {
    const created = await createTag(data)
    setTags((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)))
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
            await handleAddCard({
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

  const handleMoveSection = async (cardId, newSection) => {
    await handleUpdateCard(cardId, { section: newSection })
    setActiveSection(newSection)
  }

  const handlePageNavigate = (page, tagId) => {
    if (page === 'today')       return navigate(tagId ? `/today/tag/${tagId}` : '/today')
    if (page === 'engineering') return navigate('/engineering')
    if (page === 'workshop')    return navigate('/workshop')
    if (page === 'health')      return navigate('/health')
    if (page === 'habits')      return navigate('/health')
    return navigate(tagId ? `/${page}/tag/${tagId}` : `/${page}`)
  }

  const handleBreakdownCommit = ({ tag, cards, archived_card }) => {
    if (tag) {
      setTags((prev) => {
        if (prev.some((t) => t.id === tag.id)) return prev
        return [...prev, tag].sort((a, b) => a.name.localeCompare(b.name))
      })
    }
    setCards((prev) => {
      const updated = archived_card
        ? prev.map((t) => (t.id === archived_card.id ? archived_card : t))
        : prev
      return [...updated, ...cards]
    })
  }

  const handleModalSave = async (data) => {
    if (editingCard) {
      await handleUpdateCard(editingCard.id, data)
    } else {
      await handleAddCard(data)
    }
    closeModal()
  }

  if (authed === null) return null
  if (!authed) return <LoginPage onLogin={() => setAuthed(true)} />

  const modalContextValue = {
    openCalendarSettings: () => setShowCalendarSettings(true),
    openGithubSettings: () => setShowGithubSettings(true),
    openWithingsSettings: () => setShowWithingsSettings(true),
    openTelegramSettings: () => setShowTelegramSettings(true),
    openEdit,
  }

  return (
    <ModalContext.Provider value={modalContextValue}>
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
            <button className="btn-primary" onClick={() => { setQuickAddStep('input'); setShowQuickAdd(true) }}>
              Capture
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
                  <DropdownMenu.Item className="settings-dropdown-item" onSelect={() => setShowTelegramSettings(true)}>
                    &#9992;&#65039; Telegram Briefing
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
            cards={selectedTagId ? cards.filter((t) => (t.tags ?? []).some((tg) => tg.id === selectedTagId)) : cards}
            calendarEvents={visibleCalendarEvents}
            habits={selectedTagId ? habits.filter((h) => (h.tags ?? []).some((tg) => tg.id === selectedTagId)) : habits}
            onToggle={handleToggle}
            onToggleHabit={handleToggleHabit}
            onEdit={openEdit}
            onSave={handleUpdateCard}
            onDelete={handleDeleteCard}
            onArchive={handleArchiveCard}
            onMove={handleMoveSection}
            allTags={tags}
            onBreakdown={handleBreakdownCommit}
            onWeather={handleSetWeather}
            briefingKey={briefingKey}
            calendarReady={!calendarLoading}
            healthData={healthData}
            healthGoals={healthGoals}
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
                  <span className="mobile-tab-count">{cardsBySection[s].length}</span>
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
                    cards={cardsBySection[section]}
                    isActive={section === activeSection}
                    isMobile={isMobile}
                    onEdit={openEdit}
                    onSave={handleUpdateCard}
                    onDelete={handleDeleteCard}
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
                {activeCard ? <Card card={activeCard} isOverlay /> : null}
              </DragOverlay>
              <Archive
                cards={completedCards}
                onEdit={openEdit}
                onDelete={handleDeleteCard}
                onToggle={handleToggle}
              />
            </DndContext>
          </>
        ) : isCalendarPage ? (
          <CalendarPage
            events={visibleCalendarEvents}
            cards={visibleActiveCards}
            onToggle={handleToggle}
            onEdit={openEdit}
            onRefresh={handleRefreshCalendar}
            lastRefreshed={lastRefreshed}
            refreshing={calendarRefreshing}
            highlightEventId={highlightCalendarEventId}
            onHighlightClear={() => setHighlightCalendarEventId(null)}
          />
        ) : isHealthPage ? (
          <HealthPage
            habits={habits}
            archivedHabits={archivedHabits}
            onToggleHabit={handleToggleHabit}
            onAddHabit={handleAddHabit}
            onUpdateHabit={handleUpdateHabit}
            onDeleteHabit={handleDeleteHabit}
            onArchiveHabit={handleArchiveHabit}
            onUnarchiveHabit={handleUnarchiveHabit}
            healthData={healthData}
            healthGoals={healthGoals}
            withingsConnected={withingsStatus?.connected ?? false}
            isImperial={isImperial}
            onToggleUnit={toggleUnit}
          />
        ) : isWorkshopPage ? (
          <WorkshopPage
            cards={cards.filter(t => !t.archived && !t.completed)}
            tags={tags}
            onAddCard={handleAddCard}
          />
        ) : isEngineeringPage ? (
          <EngineeringPage
            items={engineeringItems}
            cards={cards}
            lastSynced={lastEngineeringSynced}
            syncing={engineeringSyncing}
            onSync={refreshEngineeringItems}
            onAddToBoard={async (item) => {
              await handleAddCard({
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
        <CardModal
          card={editingCard}
          defaultSection={defaultSection}
          allTags={tags}
          onClose={closeModal}
          onSave={handleModalSave}
          onDelete={editingCard ? async () => { await handleDeleteCard(editingCard.id); closeModal() } : undefined}
          onArchive={editingCard ? async () => { await handleArchiveCard(editingCard.id); closeModal() } : undefined}
        />
      )}

      {showNewSheet && (
        <CardSheet
          defaultSection={defaultSection}
          allTags={tags}
          onClose={() => setShowNewSheet(false)}
          onCreate={handleAddCard}
        />
      )}

      {showCalendarSettings && (
        <CalendarSettings
          tags={tags}
          onClose={() => {
            setShowCalendarSettings(false)
            handleRefreshCalendar()
          }}
          onDiscoverySaved={handleRefreshCalendar}
        />
      )}

      {showGithubSettings && (
        <GithubSettings
          onClose={() => setShowGithubSettings(false)}
          onSynced={() => fetchCards().then(setCards).catch(() => {})}
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

      {showTelegramSettings && (
        <TelegramSettings onClose={() => setShowTelegramSettings(false)} />
      )}

      {showTagManager && (
        <TagManagerModal
          tags={tags}
          onClose={() => setShowTagManager(false)}
          cards={cards}
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
          calendarEvents={calendarEvents}
          onSelectHabit={() => { setShowSearch(false); navigate('/health') }}
          onSelectCalendarEvent={(ev) => { setShowSearch(false); setHighlightCalendarEventId(ev.id); navigate('/calendar') }}
        />
      )}

      <KeyboardShortcutsModal
        open={showShortcuts}
        onClose={() => setShowShortcuts(false)}
        shortcuts={shortcutsRef.current}
      />

      {showQuickAdd && (
        <QuickAddModal
          allTags={visibleTags}
          visibleTags={visibleTags}
          habits={habits}
          cards={cards}
          initialStep={quickAddStep}
          onClose={() => { setShowQuickAdd(false); setQuickAddInitialText('') }}
          onSaveCard={async (data) => { await handleAddCard(data) }}
          onSaveHabit={async (data) => { await handleAddHabit(data) }}
          onSaveGoals={handleSaveWithingsGoals}
          onSaveStepGoal={handleSaveStepGoal}
          onSaveFood={createFoodEntry}
          onToggleHabit={handleToggleHabit}
          onCompleteTask={async (id) => { await handleUpdateCard(id, { completed: true }) }}
          isImperial={isImperial}
          initialText={quickAddInitialText}
        />
      )}

    </div>
    </ModalContext.Provider>
  )
}
