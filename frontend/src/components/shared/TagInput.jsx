import { useState, useRef } from 'react'
import './TagInput.css'

const PRESET_COLORS = [
  '#3b82f6', '#8b5cf6', '#ec4899', '#ef4444',
  '#f59e0b', '#10b981', '#14b8a6', '#6b7280',
]

function randomColor() {
  return PRESET_COLORS[Math.floor(Math.random() * PRESET_COLORS.length)]
}

/**
 * Tag input with chips, quick-picks, and autocomplete.
 *
 * Props:
 *   allTags       — all existing tags (for autocomplete)
 *   topTags       — top ~5 most-used tags (shown as quick-pick pills)
 *   value         — Tag[] — selected tags; pending new tags have id: null
 *   onChange      — (Tag[]) => void
 */
export default function TagInput({ allTags = [], topTags = [], value = [], onChange }) {
  const [inputValue, setInputValue] = useState('')
  const [open, setOpen] = useState(false)
  const inputRef = useRef(null)

  const selectedNames = new Set(value.map(t => t.name.toLowerCase()))

  const trimmed = inputValue.trim().replace(/,\s*$/, '')

  const matches = trimmed
    ? allTags.filter(t =>
        t.name.toLowerCase().startsWith(trimmed.toLowerCase()) &&
        !selectedNames.has(t.name.toLowerCase())
      )
    : []

  const hasExactMatch = allTags.some(t => t.name.toLowerCase() === trimmed.toLowerCase())
  const showCreate = trimmed && !hasExactMatch && !selectedNames.has(trimmed.toLowerCase())

  const addExisting = (tag) => {
    if (!selectedNames.has(tag.name.toLowerCase())) {
      onChange([...value, tag])
    }
    setInputValue('')
    setOpen(false)
    inputRef.current?.focus()
  }

  const commitInput = () => {
    const name = inputValue.trim().replace(/,\s*$/, '')
    if (!name) { setInputValue(''); return }
    if (selectedNames.has(name.toLowerCase())) { setInputValue(''); return }
    const existing = allTags.find(t => t.name.toLowerCase() === name.toLowerCase())
    onChange([...value, existing ?? { id: null, name, color: randomColor() }])
    setInputValue('')
    setOpen(false)
  }

  const removeTag = (name) =>
    onChange(value.filter(t => t.name.toLowerCase() !== name.toLowerCase()))

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      if (matches.length === 1 && !hasExactMatch) {
        addExisting(matches[0])
      } else {
        commitInput()
      }
    } else if (e.key === 'Backspace' && !inputValue && value.length > 0) {
      removeTag(value[value.length - 1].name)
    } else if (e.key === 'Escape') {
      setInputValue('')
      setOpen(false)
    }
  }

  const handleBlur = () => {
    commitInput()
    setOpen(false)
  }

  const quickPicks = topTags.filter(t => !selectedNames.has(t.name.toLowerCase()))

  return (
    <div className="tag-input">
      {value.length > 0 && (
        <div className="tag-input-chips">
          {value.map(tag => (
            <span key={tag.name} className="tag-chip" style={{ background: tag.color }}>
              {tag.name}
              <button
                type="button"
                className="tag-chip-remove"
                onClick={() => removeTag(tag.name)}
                aria-label={`Remove ${tag.name}`}
              >×</button>
            </span>
          ))}
        </div>
      )}

      {quickPicks.length > 0 && (
        <div className="tag-input-quickpicks">
          {quickPicks.map(tag => (
            <button
              key={tag.id}
              type="button"
              className="tag-quickpick"
              style={{ '--tag-color': tag.color }}
              onClick={() => addExisting(tag)}
            >
              {tag.name}
            </button>
          ))}
        </div>
      )}

      <div className="tag-input-field">
        <input
          ref={inputRef}
          type="text"
          className="tag-input-text"
          placeholder={value.length === 0 ? 'Add tags…' : 'Add another…'}
          value={inputValue}
          onChange={e => { setInputValue(e.target.value); setOpen(true) }}
          onKeyDown={handleKeyDown}
          onFocus={() => trimmed && setOpen(true)}
          onBlur={handleBlur}
        />
        {open && (matches.length > 0 || showCreate) && (
          <ul className="tag-input-dropdown" role="listbox">
            {matches.map(tag => (
              <li key={tag.id} role="option">
                <button
                  type="button"
                  className="tag-input-option"
                  onMouseDown={e => e.preventDefault()}
                  onClick={() => addExisting(tag)}
                >
                  <span className="tag-input-dot" style={{ background: tag.color }} />
                  {tag.name}
                </button>
              </li>
            ))}
            {showCreate && (
              <li role="option">
                <button
                  type="button"
                  className="tag-input-option tag-input-option--create"
                  onMouseDown={e => e.preventDefault()}
                  onClick={commitInput}
                >
                  + Create "{trimmed}"
                </button>
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  )
}
