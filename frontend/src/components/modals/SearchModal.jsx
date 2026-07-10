import { useState, useEffect, useRef } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { MagnifyingGlassIcon } from '@radix-ui/react-icons'
import { searchCards } from '../../api'
import './SearchModal.css'
import { SECTION_LABELS } from '../../lib/sections'
const SECTION_COLORS = { today: '#3b82f6', week: '#8b5cf6', month: '#f59e0b', later: '#6b7280' }

const EVENT_COLOR = '#0ea5e9'

function fmtEventDate(start) {
  if (!start) return null
  const d = new Date(start)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export default function SearchModal({ onClose, onEdit, habits = [], calendarEvents = [], onSelectHabit, onSelectCalendarEvent }) {
  const [query, setQuery] = useState('')
  const [cardResults, setCardResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const activeIndexRef = useRef(-1)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const debounceRef = useRef(null)

  const updateActiveIndex = (fn) => {
    setActiveIndex((prev) => {
      const next = typeof fn === 'function' ? fn(prev) : fn
      activeIndexRef.current = next
      return next
    })
  }

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const habitResults = query.trim()
    ? habits.filter((h) => !h.archived && h.name.toLowerCase().includes(query.trim().toLowerCase()))
    : []

  const eventResults = query.trim()
    ? calendarEvents
        .filter((e) => {
          const q = query.trim().toLowerCase()
          return e.title?.toLowerCase().includes(q) || e.description?.toLowerCase().includes(q)
        })
        .slice()
        .sort((a, b) => new Date(a.start) - new Date(b.start))
    : []

  const results = [
    ...cardResults.map((r) => ({ ...r, _type: 'card' })),
    ...habitResults.map((h) => ({ ...h, _type: 'habit' })),
    ...eventResults.map((e) => ({ ...e, _type: 'event' })),
  ]

  useEffect(() => {
    clearTimeout(debounceRef.current)
    if (!query.trim()) { setCardResults([]); return }
    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const data = await searchCards(query.trim())
        setCardResults(data)
      } catch {
        setCardResults([])
      } finally {
        setLoading(false)
      }
    }, 200)
  }, [query])

  // Reset selection when results change
  useEffect(() => { updateActiveIndex(-1) }, [cardResults, habitResults.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll active item into view
  useEffect(() => {
    if (activeIndex < 0 || !listRef.current) return
    const items = listRef.current.querySelectorAll('.search-result')
    items[activeIndex]?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  const handleSelect = (result) => {
    if (result._type === 'habit') {
      onSelectHabit?.(result)
    } else if (result._type === 'event') {
      onSelectCalendarEvent?.(result)
    } else {
      onClose()
      onEdit(result)
    }
  }

  const handleKeyDown = (e) => {
    if (results.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      updateActiveIndex((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      updateActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const idx = activeIndexRef.current
      if (idx >= 0 && results[idx]) handleSelect(results[idx])
    }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="search-modal" aria-describedby={undefined}>
          <Dialog.Title className="sr-only">Search</Dialog.Title>
          <div className="search-input-wrap">
            <MagnifyingGlassIcon className="search-icon" />
            <input
              ref={inputRef}
              className="search-input"
              placeholder="Search..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              autoComplete="off"
            />
            {loading && <span className="search-spinner" />}
          </div>

          {results.length > 0 && (
            <div className="search-results" ref={listRef}>
              {results.map((result, i) => (
                <button
                  key={`${result._type}-${result.id}`}
                  className={[
                    'search-result',
                    result.completed ? 'search-result--done' : '',
                    i === activeIndex ? 'search-result--active' : '',
                  ].filter(Boolean).join(' ')}
                  onClick={() => handleSelect(result)}
                  onMouseEnter={() => updateActiveIndex(i)}
                >
                  <div className="search-result-main">
                    {result._type === 'habit' ? (
                      <span className="search-result-section" style={{ background: '#7c3aed' }}>Habit</span>
                    ) : result._type === 'event' ? (
                      <span className="search-result-section" style={{ background: EVENT_COLOR }}>Event</span>
                    ) : (
                      <span
                        className="search-result-section"
                        style={{ background: SECTION_COLORS[result.section] ?? '#6b7280' }}
                      >
                        {SECTION_LABELS[result.section] ?? 'Card'}
                      </span>
                    )}
                    <span className="search-result-title">{result._type === 'habit' ? result.name : result.title}</span>
                    {result._type === 'event' && result.start && (
                      <span className="search-result-date">{fmtEventDate(result.start)}</span>
                    )}
                  </div>
                  {result._type === 'card' && result.description && (
                    <span className="search-result-desc">{result.description}</span>
                  )}
                  {(result.tags ?? []).length > 0 && (
                    <div className="search-result-tags">
                      {result.tags.map((tag) => (
                        <span
                          key={tag.id}
                          className="search-result-tag"
                          style={{ background: tag.color }}
                        >
                          {tag.name}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}

          {query.trim() && !loading && results.length === 0 && (
            <div className="search-empty">No results for "{query}"</div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
