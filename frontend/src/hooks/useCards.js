import { useState, useEffect, useRef } from 'react'
import {
  fetchCards,
  createCard,
  updateCard,
  deleteCard,
  addTagToCard,
  removeTagFromCard,
  updateTag,
  deleteTag,
  replaceTag,
} from '../api'

export function useCards({ authed, tags, setTags, invalidateBriefing }) {
  const [todos, setTodos] = useState([])
  const [loading, setLoading] = useState(true)
  const todosRef = useRef(todos)
  todosRef.current = todos

  useEffect(() => {
    if (!authed) return
    fetchCards()
      .then(setTodos)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [authed])

  const handleAddTodo = async (data) => {
    const created = await createCard(data)
    setTodos((prev) => [...prev, created])
    invalidateBriefing?.()
    return created
  }

  const handleUpdateTodo = async (id, data) => {
    const updated = await updateCard(id, data)
    setTodos((prev) => prev.map((t) => (t.id === id ? updated : t)))
    return updated
  }

  const handleDeleteTodo = async (id) => {
    await deleteCard(id)
    setTodos((prev) => prev.filter((t) => t.id !== id))
  }

  const handleToggle = async (todo) => {
    await handleUpdateTodo(todo.id, { completed: !todo.completed })
    invalidateBriefing?.()
  }

  const handleAddTag = async (todoId, tagId) => {
    await addTagToCard(todoId, tagId)
    const tag = tags.find((t) => t.id === tagId)
    if (!tag) return
    setTodos((prev) =>
      prev.map((t) =>
        t.id === todoId ? { ...t, tags: [...(t.tags ?? []), tag] } : t
      )
    )
  }

  const handleRemoveTag = async (todoId, tagId) => {
    await removeTagFromCard(todoId, tagId)
    setTodos((prev) =>
      prev.map((t) =>
        t.id === todoId ? { ...t, tags: (t.tags ?? []).filter((tg) => tg.id !== tagId) } : t
      )
    )
  }

  const handleAddCard = async (data) => {
    return handleAddTodo({ ...data, section: data.section ?? 'later' })
  }

  const handleUpdateCard = (id, data) => handleUpdateTodo(id, data)

  const handleDeleteCard = (id) => handleDeleteTodo(id)

  const handleArchiveCard = (id) => handleUpdateTodo(id, { archived: true })

  const handleUnarchiveCard = (id) => handleUpdateTodo(id, { archived: false })

  const handleUpdateTag = async (tagId, data) => {
    const updated = await updateTag(tagId, data)
    setTags((prev) => prev.map((t) => (t.id === tagId ? updated : t)).sort((a, b) => a.name.localeCompare(b.name)))
    setTodos((prev) =>
      prev.map((t) => ({ ...t, tags: (t.tags ?? []).map((tg) => (tg.id === tagId ? updated : tg)) }))
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
        const hasFrom = (todo.tags ?? []).some((tg) => tg.id === fromTagId)
        if (!hasFrom) return todo
        const hasTo = (todo.tags ?? []).some((tg) => tg.id === toTagId)
        const filtered = (todo.tags ?? []).filter((tg) => tg.id !== fromTagId)
        return { ...todo, tags: hasTo ? filtered : [...filtered, toTag] }
      })
    )
  }

  return {
    todos,
    setTodos,
    loading,
    todosRef,
    handleAddTodo,
    handleUpdateTodo,
    handleDeleteTodo,
    handleToggle,
    handleAddTag,
    handleRemoveTag,
    handleAddCard,
    handleUpdateCard,
    handleDeleteCard,
    handleArchiveCard,
    handleUnarchiveCard,
    handleUpdateTag,
    handleDeleteTag,
    handleReplaceTag,
  }
}
