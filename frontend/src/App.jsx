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
import Column from './components/Column'
import TodoCard from './components/TodoCard'
import AddTodoModal from './components/AddTodoModal'
import Archive from './components/Archive'
import QuickAddModal from './components/QuickAddModal'
import QueueIndicator from './components/QueueIndicator'
import SearchModal from './components/SearchModal'
import TagManagerModal from './components/TagManagerModal'
import CalendarSettings from './components/CalendarSettings'
import CalendarStrip from './components/CalendarStrip'
import CalendarPage from './components/CalendarPage'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { GearIcon, MagnifyingGlassIcon } from '@radix-ui/react-icons'
import { useNotifications } from './hooks/useNotifications'
import DailyBriefing from './components/DailyBriefing'
import HabitTracker from './components/HabitTracker'
import HabitsPage from './components/HabitsPage'
import NotesPage from './components/NotesPage'
import TodayPage from './components/TodayPage'
import Sidebar from './components/Sidebar'
import MobileNav from './components/MobileNav'
import TagFilterBar from './components/TagFilterBar'
import LoginPage from './components/LoginPage'
import { fetchTodos, fetchTags, createTodo, updateTodo, deleteTodo, reorderTodos, addTagToTodo, removeTagFromTodo, createTag, updateTag, deleteTag, replaceTag, parseTodo, fetchCalendarEvents, fetchHabits, createHabit, updateHabit, deleteHabit, checkHabit, uncheckHabit, fetchNotes, createNote, updateNote, deleteNote, promoteNote, checkAuth, logout } from './api'
import './App.css'

export const SECTIONS = ['today', 'week', 'month', 'later']
export const SECTION_LABELS = {
  today: 'Today',
  week: 'This Week',
  month: 'This Month',
  later: 'Later',
}
const SECTION_COLORS = {
  today: '#3b82f6',
  week: '#8b5cf6',
  month: '#f59e0b',
  later: '#6b7280',
}

export default function App() {
  const [authed, setAuthed] = useState(null)   // null=checking, true/false
  const [authEnabled, setAuthEnabled] = useState(false)
  const [todos, setTodos] = useState([])
  const [tags, setTags] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [showQuickAdd, setShowQuickAdd] = useState(false)
  const [showSearch, setShowSearch] = useState(false)
  const [showTagManager, setShowTagManager] = useState(false)
  const [showCalendarSettings, setShowCalendarSettings] = useState(false)
  const [calendarEvents, setCalendarEvents] = useState([])
  const [lastRefreshed, setLastRefreshed] = useState(null)
  const [calendarRefreshing, setCalendarRefreshing] = useState(false)
  const [editingTodo, setEditingTodo] = useState(null)
  const [activeTodo, setActiveTodo] = useState(null)
  const [activeSection, setActiveSection] = useState('today')
  const [weather, setWeather] = useState(null)
  const [habits, setHabits] = useState([])
  const [notes, setNotes] = useState([])

  const { permission: notifPermission, enabled: notifEnabled, setEnabled: setNotifEnabled, requestPermission } = useNotifications(
    todos,
    (todoId) => {
      const todo = todosRef.current.find((t) => t.id === todoId)
      if (todo) openEdit(todo)
    }
  )

  const navigate = useNavigate()
  const location = useLocation()
  const isTodayPage    = location.pathname === '/today'
  const isHabitsPage   = location.pathname === '/habits'   || location.pathname.startsWith('/habits/tag/')
  const isTasksPage    = location.pathname === '/tasks'    || location.pathname.startsWith('/tasks/tag/')
  const isCalendarPage = location.pathname === '/calendar' || location.pathname.startsWith('/calendar/tag/')
  const isNotesPage    = location.pathname === '/notes'    || location.pathname.startsWith('/notes/tag/')
  const currentPage    = isTodayPage ? 'today' : isHabitsPage ? 'habits' : isTasksPage ? 'tasks' : isCalendarPage ? 'calendar' : isNotesPage ? 'notes' : 'overview'

  const tagMatch =
    location.pathname.match(/^\/tag\/(\d+)$/)          ||
    location.pathname.match(/^\/habits\/tag\/(\d+)$/)   ||
    location.pathname.match(/^\/tasks\/tag\/(\d+)$/)    ||
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
    const handler = (e) => {
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const tag = document.activeElement?.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
        e.preventDefault()
        setShowSearch(true)
      }
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
  const todosRef = useRef(todos)
  todosRef.current = todos
  const tagsRef = useRef(tags)
  tagsRef.current = tags

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: isMobile ? 999999 : 8 } })
  )

  useEffect(() => {
    checkAuth().then(({ authed: a, enabled: e }) => {
      setAuthed(a)
      setAuthEnabled(e)
    })
  }, [])

  useEffect(() => {
    if (!authed) return
    Promise.all([fetchTodos(), fetchTags(), fetchHabits(), fetchNotes()])
      .then(([todosData, tagsData, habitsData, notesData]) => {
        setTodos(todosData)
        setTags(tagsData)
        setHabits(habitsData)
        setNotes(notesData)
      })
      .finally(() => setLoading(false))

    fetchCalendarEvents()
      .then((events) => { setCalendarEvents(events); setLastRefreshed(new Date()) })
      .catch(() => {})
  }, [authed])

  // Poll for calendar updates every 15 minutes
  useEffect(() => {
    const id = setInterval(() => {
      fetchCalendarEvents()
        .then((events) => { setCalendarEvents(events); setLastRefreshed(new Date()) })
        .catch(() => {})
    }, 15 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  const activeTodos = todos.filter((t) => !t.completed)
  const completedTodos = todos.filter((t) => t.completed)

  const visibleCalendarEvents = selectedTagId === null
    ? calendarEvents
    : calendarEvents.filter((e) => e.tag_id === selectedTagId)

  const visibleActiveTodos = selectedTagId === null
    ? activeTodos
    : activeTodos.filter((t) => (t.tags ?? []).some((tag) => tag.id === selectedTagId))

  const todosBySection = SECTIONS.reduce((acc, s) => {
    acc[s] = visibleActiveTodos
      .filter((t) => t.section === s)
      .sort((a, b) => a.position - b.position)
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

    const activeSection = dragged.section
    const overSection = SECTIONS.includes(String(over.id))
      ? String(over.id)
      : current.find((t) => t.id === over.id)?.section

    if (!overSection || activeSection === overSection) return

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

    const section = dragged.section
    const sectionTodos = current
      .filter((t) => t.section === section)
      .sort((a, b) => a.position - b.position)

    let reordered = sectionTodos

    // Reorder within section if dropped on a sibling card
    if (!SECTIONS.includes(String(over.id))) {
      const overTodo = current.find((t) => t.id === over.id)
      if (overTodo?.section === section) {
        const fromIdx = sectionTodos.findIndex((t) => t.id === active.id)
        const toIdx = sectionTodos.findIndex((t) => t.id === over.id)
        if (fromIdx !== -1 && toIdx !== -1 && fromIdx !== toIdx) {
          reordered = arrayMove(sectionTodos, fromIdx, toIdx)
        }
      }
    }

    const updatedSection = reordered.map((t, i) => ({ ...t, position: i }))
    const newTodos = [
      ...current.filter((t) => t.section !== section),
      ...updatedSection,
    ]

    setTodos(newTodos)
    reorderTodos(
      updatedSection.map(({ id, section, position }) => ({ id, section, position }))
    )
  }, [])

  const handleAddTodo = async (data) => {
    const created = await createTodo(data)
    setTodos((prev) => [...prev, created])
  }

  const handleUpdateTodo = async (id, data) => {
    const updated = await updateTodo(id, data)
    setTodos((prev) => prev.map((t) => (t.id === id ? updated : t)))
  }

  const handleDeleteTodo = async (id) => {
    await deleteTodo(id)
    setTodos((prev) => prev.filter((t) => t.id !== id))
  }

  const handleToggle = async (todo) => {
    await handleUpdateTodo(todo.id, { completed: !todo.completed })
  }

  const handleAddTag = async (todoId, tagId) => {
    await addTagToTodo(todoId, tagId)
    const tag = tags.find((t) => t.id === tagId)
    if (!tag) return
    setTodos((prev) =>
      prev.map((t) =>
        t.id === todoId ? { ...t, tags: [...(t.tags ?? []), tag] } : t
      )
    )
  }

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

  const handleAddHabit = async (data) => {
    const habit = await createHabit(data)
    setHabits((prev) => [...prev, habit])
  }

  const handleUpdateHabit = async (id, data) => {
    const updated = await updateHabit(id, data)
    setHabits((prev) => prev.map((h) => (h.id === id ? updated : h)))
  }

  const handleDeleteHabit = async (id) => {
    setHabits((prev) => prev.filter((h) => h.id !== id))
    await deleteHabit(id)
  }

  const handleToggleHabit = async (habit) => {
    const wasChecked = habit.completed_today
    setHabits((prev) =>
      prev.map((h) =>
        h.id === habit.id
          ? { ...h, completed_today: !wasChecked, streak: !wasChecked ? h.streak + 1 : Math.max(0, h.streak - 1) }
          : h
      )
    )
    try {
      if (wasChecked) {
        await uncheckHabit(habit.id)
      } else {
        await checkHabit(habit.id)
      }
      const updated = await fetchHabits()
      setHabits(updated)
    } catch {
      setHabits((prev) => prev.map((h) => (h.id === habit.id ? habit : h)))
    }
  }

  const handleRemoveTag = async (todoId, tagId) => {
    await removeTagFromTodo(todoId, tagId)
    setTodos((prev) =>
      prev.map((t) =>
        t.id === todoId ? { ...t, tags: (t.tags ?? []).filter((tg) => tg.id !== tagId) } : t
      )
    )
  }

  const processQueueItem = (id, text) => {
    parseTodo(text)
      .then(async (result) => {
        const tagIds = (result.suggested_tags ?? [])
          .map((name) => tagsRef.current.find((t) => t.name.toLowerCase() === name.toLowerCase())?.id)
          .filter(Boolean)
        try {
          if (result.type === 'habit') {
            await handleAddHabit({ name: result.title, tag_ids: tagIds })
          } else if (result.type === 'note') {
            await handleAddNote({
              title: result.title || null,
              content: result.note_content || result.description || text,
              tag_ids: tagIds,
            })
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

  const handleRefreshCalendar = async () => {
    setCalendarRefreshing(true)
    try {
      const events = await fetchCalendarEvents()
      setCalendarEvents(events)
      setLastRefreshed(new Date())
    } catch {
      // ignore
    } finally {
      setCalendarRefreshing(false)
    }
  }

  const handleMoveSection = async (todoId, newSection) => {
    await handleUpdateTodo(todoId, { section: newSection })
    setActiveSection(newSection)
  }

  const handleAddNote = async (data) => {
    const created = await createNote(data)
    setNotes((prev) => [created, ...prev])
    return created
  }

  const handleUpdateNote = async (id, data) => {
    const updated = await updateNote(id, data)
    setNotes((prev) => prev.map((n) => (n.id === id ? updated : n)))
    return updated
  }

  const handleDeleteNote = async (id) => {
    await deleteNote(id)
    setNotes((prev) => prev.filter((n) => n.id !== id))
  }

  const handlePromoteNote = async (id) => {
    const todo = await promoteNote(id)
    setTodos((prev) => [...prev, todo])
    return todo
  }

  const handlePageNavigate = (page, tagId) => {
    if (page === 'today')    return navigate('/today')
    if (page === 'habits')   return navigate(tagId ? `/habits/tag/${tagId}` : '/habits')
    if (page === 'overview') return navigate(tagId ? `/tag/${tagId}` : '/')
    return navigate(tagId ? `/${page}/tag/${tagId}` : `/${page}`)
  }

  const openEdit = (todo) => {
    setEditingTodo(todo)
    setShowModal(true)
  }

  const closeModal = () => {
    setShowModal(false)
    setEditingTodo(null)
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
                  <DropdownMenu.Separator className="settings-dropdown-divider" />
                  <DropdownMenu.Item className="settings-dropdown-item" onSelect={() => setShowModal(true)}>
                    + Add Task (Advanced)
                  </DropdownMenu.Item>
                  {authEnabled && (
                    <>
                      <DropdownMenu.Separator className="settings-dropdown-divider" />
                      <DropdownMenu.Item
                        className="settings-dropdown-item"
                        onSelect={async () => { await logout(); setAuthed(false) }}
                      >
                        &#x1F512; Sign out
                      </DropdownMenu.Item>
                    </>
                  )}
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          </div>
        </div>
      </header>

      <div className="app-body">
        <Sidebar
          tags={tags}
          selectedTagId={selectedTagId}
          page={currentPage}
          onNavigate={handlePageNavigate}
        />

      <main className="board-wrapper">
        {!isTodayPage && (
          <TagFilterBar
            tags={tags}
            selectedTagId={selectedTagId}
            page={currentPage}
            onNavigate={handlePageNavigate}
          />
        )}
        {loading ? (
          <div className="loading">Loading...</div>
        ) : isTodayPage ? (
          <TodayPage
            todos={todos}
            calendarEvents={calendarEvents}
            habits={habits}
            onToggle={handleToggle}
            onToggleHabit={handleToggleHabit}
            onEdit={openEdit}
            onDelete={handleDeleteTodo}
            onMove={handleMoveSection}
          />
        ) : isHabitsPage ? (
          <HabitsPage
            habits={habits}
            allTags={tags}
            selectedTagId={selectedTagId}
            onToggle={handleToggleHabit}
            onAdd={handleAddHabit}
            onUpdate={handleUpdateHabit}
            onDelete={handleDeleteHabit}
          />
        ) : isNotesPage ? (
          <NotesPage
            notes={selectedTagId === null ? notes : notes.filter((n) => (n.tags ?? []).some((t) => t.id === selectedTagId))}
            allTags={tags}
            onAdd={handleAddNote}
            onUpdate={handleUpdateNote}
            onDelete={handleDeleteNote}
            onPromote={handlePromoteNote}
          />
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
        ) : (
          <>
          <DailyBriefing
            todos={visibleActiveTodos}
            calendarEvents={visibleCalendarEvents}
            habits={habits}
            tagId={selectedTagId}
            ready={!loading}
            onWeather={setWeather}
          />
          {!isTasksPage && (
            <HabitTracker
              habits={habits}
              onToggle={handleToggleHabit}
            />
          )}

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
                  onDelete={handleDeleteTodo}
                  onToggle={handleToggle}
                  onMove={handleMoveSection}
                />
              ))}
            </div>

            <DragOverlay dropAnimation={null}>
              {activeTodo ? (
                <TodoCard todo={activeTodo} isOverlay />
              ) : null}
            </DragOverlay>
          </DndContext>

          {!isTasksPage && (
            <CalendarStrip
              events={visibleCalendarEvents}
              onRefresh={handleRefreshCalendar}
              lastRefreshed={lastRefreshed}
              refreshing={calendarRefreshing}
              activeSection={activeSection}
            />
          )}

          <Archive
            todos={completedTodos}
            onDelete={handleDeleteTodo}
            onToggle={handleToggle}
          />
          </>
        )}
      </main>
      </div>{/* app-body */}

      <MobileNav
        page={currentPage}
        onNavigate={handlePageNavigate}
      />

      {showModal && (
        <AddTodoModal
          todo={editingTodo}
          allTags={tags}
          onClose={closeModal}
          onSave={handleModalSave}
          onDelete={editingTodo ? async () => { await handleDeleteTodo(editingTodo.id); closeModal() } : undefined}
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
        />
      )}

      {showQuickAdd && (
        <QuickAddModal
          onClose={() => setShowQuickAdd(false)}
          onSubmit={(text) => {
            addToParseQueue(text)
            setShowQuickAdd(false)
          }}
        />
      )}

    </div>
  )
}
