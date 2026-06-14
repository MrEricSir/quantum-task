import { useState, useEffect, useRef } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { MagnifyingGlassIcon } from '@radix-ui/react-icons'
import { searchTodos } from '../../api'
import './SearchModal.css'

const SECTION_LABELS = { today: 'Today', week: 'This Week', month: 'This Month', later: 'Later' }
const SECTION_COLORS = { today: '#3b82f6', week: '#8b5cf6', month: '#f59e0b', later: '#6b7280' }

export default function SearchModal({ onClose, onEdit }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
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

  useEffect(() => {
    clearTimeout(debounceRef.current)
    if (!query.trim()) { setResults([]); return }
    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const data = await searchTodos(query.trim())
        setResults(data)
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 200)
  }, [query])

  // Reset selection when results change
  useEffect(() => { updateActiveIndex(-1) }, [results]) // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll active item into view
  useEffect(() => {
    if (activeIndex < 0 || !listRef.current) return
    const items = listRef.current.querySelectorAll('.search-result')
    items[activeIndex]?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  const handleSelect = (todo) => {
    onClose()
    onEdit(todo)
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
          <Dialog.Title className="sr-only">Search tasks</Dialog.Title>
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
              {results.map((todo, i) => (
                <button
                  key={todo.id}
                  className={[
                    'search-result',
                    todo.completed ? 'search-result--done' : '',
                    i === activeIndex ? 'search-result--active' : '',
                  ].filter(Boolean).join(' ')}
                  onClick={() => handleSelect(todo)}
                  onMouseEnter={() => updateActiveIndex(i)}
                >
                  <div className="search-result-main">
                    <span
                      className="search-result-section"
                      style={{ background: SECTION_COLORS[todo.section] }}
                    >
                      {SECTION_LABELS[todo.section]}
                    </span>
                    <span className="search-result-title">{todo.title}</span>
                  </div>
                  {todo.description && (
                    <span className="search-result-desc">{todo.description}</span>
                  )}
                  {(todo.tags ?? []).length > 0 && (
                    <div className="search-result-tags">
                      {todo.tags.map((tag) => (
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
