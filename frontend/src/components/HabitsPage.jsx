import { useState, useRef, useEffect } from 'react'
import { Pencil1Icon, CheckIcon, Cross2Icon } from '@radix-ui/react-icons'
import Collapsible from './Collapsible'
import './HabitsPage.css'

function HabitsArchive({ habits, onUnarchive, onDelete }) {
  if (habits.length === 0) return null
  return (
    <div className="habits-archive">
      <Collapsible label="Habit Archive" count={habits.length} defaultOpen={true}>
        <div className="habits-archive-list">
          {habits.map((habit) => (
            <div key={habit.id} className="habits-archive-row">
              <span className="habits-archive-row-name">{habit.name}</span>
              <div className="habits-archive-row-actions">
                <button className="habits-archive-btn" onClick={() => onUnarchive(habit.id)} title="Restore habit">
                  Restore
                </button>
                <button
                  className="habits-archive-btn habits-archive-btn--delete"
                  onClick={() => onDelete(habit.id)}
                  aria-label="Delete habit"
                  title="Delete permanently"
                >
                  <Cross2Icon />
                </button>
              </div>
            </div>
          ))}
        </div>
      </Collapsible>
    </div>
  )
}

export default function HabitsPage({ habits, archivedHabits = [], allTags, selectedTagId = null, onToggle, onAdd, onUpdate, onDelete, onArchive, onUnarchive }) {
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [newTagIds, setNewTagIds] = useState([])
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const addInputRef = useRef(null)
  const editInputRef = useRef(null)

  useEffect(() => {
    if (adding) addInputRef.current?.focus()
  }, [adding])

  useEffect(() => {
    if (editingId !== null) editInputRef.current?.focus()
  }, [editingId])

  const confirmAdd = async () => {
    const name = newName.trim()
    if (!name) return
    try {
      await onAdd({ name, tag_ids: newTagIds })
      setNewName('')
      setNewTagIds([])
      setAdding(false)
    } catch {
      // leave form open so user can retry
    }
  }

  const cancelAdd = () => {
    setAdding(false)
    setNewName('')
    setNewTagIds([])
  }

  const startEdit = (habit) => {
    setEditingId(habit.id)
    setEditName(habit.name)
  }

  const confirmEdit = async () => {
    const name = editName.trim()
    if (name) await onUpdate(editingId, { name })
    setEditingId(null)
  }

  const toggleNewTag = (id) =>
    setNewTagIds((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]))

  const visibleHabits = selectedTagId === null
    ? habits
    : habits.filter((h) => h.tags.some((t) => t.id === selectedTagId))

  const done = visibleHabits.filter((h) => h.completed_today).length

  const hasTags = allTags.length > 0

  return (
    <div className="habits-page">
      <div className="habits-page-header">
        <div className="habits-page-title-row">
          <h2 className="habits-page-title">Habits</h2>
          {visibleHabits.length > 0 && (
            <span className="habits-page-progress">{done} / {visibleHabits.length} today</span>
          )}
        </div>
        {!adding && (
          <button className="btn-primary" onClick={() => setAdding(true)}>
            + Add habit
          </button>
        )}
      </div>


      {adding && (
        <div className="habits-add-card">
          <input
            ref={addInputRef}
            className="habits-add-input"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') confirmAdd()
              if (e.key === 'Escape') cancelAdd()
            }}
            placeholder="Habit name..."
          />
          {hasTags && (
            <div className="habits-add-tags">
              <span className="habits-add-tags-label">Tags (optional):</span>
              {allTags.map((tag) => (
                <button
                  type="button"
                  key={tag.id}
                  className={`habit-tag-pill${newTagIds.includes(tag.id) ? ' habit-tag-pill--active' : ''}`}
                  style={
                    newTagIds.includes(tag.id)
                      ? { background: tag.color, borderColor: tag.color, color: '#fff' }
                      : { borderColor: tag.color, color: tag.color }
                  }
                  onClick={() => toggleNewTag(tag.id)}
                >
                  {tag.name}
                </button>
              ))}
            </div>
          )}
          <div className="habits-add-actions">
            <button type="button" className="btn-primary" onClick={confirmAdd} disabled={!newName.trim()}>
              Add
            </button>
            <button type="button" className="btn-secondary" onClick={cancelAdd}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {visibleHabits.length === 0 && !adding ? (
        <div className="habits-empty">
          {selectedTagId !== null
            ? 'No habits with this tag.'
            : 'No habits yet. Add your first one to start tracking daily streaks.'}
        </div>
      ) : (
        <div className="habits-list">
          {visibleHabits.map((habit) => (
            <div key={habit.id} className={`habit-card${habit.completed_today ? ' habit-card--done' : ''}`}>
              <button
                type="button"
                className="habit-card-check"
                onClick={() => onToggle(habit)}
                aria-label={habit.completed_today ? 'Mark incomplete' : 'Mark complete'}
              >
                {habit.completed_today ? <CheckIcon width={13} height={13} /> : null}
              </button>

              <div className="habit-card-body">
                {editingId === habit.id ? (
                  <input
                    ref={editInputRef}
                    className="habit-card-edit-input"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') confirmEdit()
                      if (e.key === 'Escape') setEditingId(null)
                    }}
                    onBlur={confirmEdit}
                  />
                ) : (
                  <span className="habit-card-name">{habit.name}</span>
                )}

                {habit.tags.length > 0 && (
                  <div className="habit-card-tags">
                    {habit.tags.map((tag) => (
                      <span
                        key={tag.id}
                        className="habit-card-tag"
                        style={{ background: `${tag.color}22`, color: tag.color, borderColor: `${tag.color}44` }}
                      >
                        {tag.name}
                      </span>
                    ))}
                  </div>
                )}

                {habit.recent_completions?.length > 0 && (
                  <div className="habit-card-history">
                    <div className="habit-dots">
                      {habit.recent_completions.map((done, i) => (
                        <span
                          key={i}
                          className={`habit-dot${done ? ' habit-dot--done' : ''}${i === 6 ? ' habit-dot--today' : ''}`}
                        />
                      ))}
                    </div>
                    {habit.streak > 0 && (
                      <span className="habit-card-streak">🔥 {habit.streak}</span>
                    )}
                  </div>
                )}
              </div>

              <div className="habit-card-actions">
                <button
                  type="button"
                  className="habit-card-btn"
                  onClick={() => startEdit(habit)}
                  aria-label="Edit habit"
                  title="Edit name"
                >
                  <Pencil1Icon width={13} height={13} />
                </button>
                <button
                  type="button"
                  className="habit-card-btn habit-card-btn--archive"
                  onClick={() => onArchive(habit.id)}
                  aria-label="Archive habit"
                  title="Archive habit"
                >
                  <svg width="13" height="13" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="1" y="1" width="13" height="3.5" rx="0.5" stroke="currentColor" strokeWidth="1.2"/>
                    <path d="M1.5 4.5v8.5a.5.5 0 00.5.5h11a.5.5 0 00.5-.5V4.5" stroke="currentColor" strokeWidth="1.2"/>
                    <path d="M5.5 8.5l2 2 2-2M7.5 10.5V7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <HabitsArchive
        habits={archivedHabits}
        onUnarchive={onUnarchive}
        onDelete={onDelete}
      />

    </div>
  )
}
