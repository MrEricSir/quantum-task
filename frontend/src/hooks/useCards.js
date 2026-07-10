import { useCallback, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
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

export const CARDS_QUERY_KEY = ['cards']

export function useCards({ authed, tags, setTags, invalidateBriefing }) {
  const queryClient = useQueryClient()

  const { data: cards = [], isLoading: loading } = useQuery({
    queryKey: CARDS_QUERY_KEY,
    queryFn: fetchCards,
    enabled: !!authed,
  })

  // Keep a ref so memoized DnD callbacks can access fresh state without
  // re-creating the callbacks on every render.
  const cardsRef = useRef(cards)
  cardsRef.current = cards

  const setCards = useCallback(
    (updater) => queryClient.setQueryData(CARDS_QUERY_KEY, updater),
    [queryClient],
  )

  const handleAddCard = async (data) => {
    const created = await createCard({ ...data, section: data.section ?? 'later' })
    queryClient.setQueryData(CARDS_QUERY_KEY, (prev) => [...(prev ?? []), created])
    invalidateBriefing?.()
    return created
  }

  const handleUpdateCard = async (id, data) => {
    const updated = await updateCard(id, data)
    queryClient.setQueryData(CARDS_QUERY_KEY, (prev) =>
      (prev ?? []).map((t) => (t.id === id ? updated : t)),
    )
    return updated
  }

  const handleDeleteCard = async (id) => {
    await deleteCard(id)
    queryClient.setQueryData(CARDS_QUERY_KEY, (prev) =>
      (prev ?? []).filter((t) => t.id !== id),
    )
  }

  const handleToggle = async (card) => {
    await handleUpdateCard(card.id, { completed: !card.completed })
    invalidateBriefing?.()
  }

  const handleAddTag = async (cardId, tagId) => {
    await addTagToCard(cardId, tagId)
    const tag = tags.find((t) => t.id === tagId)
    if (!tag) return
    queryClient.setQueryData(CARDS_QUERY_KEY, (prev) =>
      (prev ?? []).map((t) =>
        t.id === cardId ? { ...t, tags: [...(t.tags ?? []), tag] } : t,
      ),
    )
  }

  const handleRemoveTag = async (cardId, tagId) => {
    await removeTagFromCard(cardId, tagId)
    queryClient.setQueryData(CARDS_QUERY_KEY, (prev) =>
      (prev ?? []).map((t) =>
        t.id === cardId ? { ...t, tags: (t.tags ?? []).filter((tg) => tg.id !== tagId) } : t,
      ),
    )
  }

  const handleArchiveCard = (id) => handleUpdateCard(id, { archived: true })
  const handleUnarchiveCard = (id) => handleUpdateCard(id, { archived: false })

  const handleUpdateTag = async (tagId, data) => {
    const updated = await updateTag(tagId, data)
    setTags((prev) =>
      prev.map((t) => (t.id === tagId ? updated : t)).sort((a, b) => a.name.localeCompare(b.name)),
    )
    queryClient.setQueryData(CARDS_QUERY_KEY, (prev) =>
      (prev ?? []).map((t) => ({
        ...t,
        tags: (t.tags ?? []).map((tg) => (tg.id === tagId ? updated : tg)),
      })),
    )
  }

  const handleDeleteTag = async (tagId) => {
    await deleteTag(tagId)
    setTags((prev) => prev.filter((t) => t.id !== tagId))
    queryClient.setQueryData(CARDS_QUERY_KEY, (prev) =>
      (prev ?? []).map((t) => ({
        ...t,
        tags: (t.tags ?? []).filter((tg) => tg.id !== tagId),
      })),
    )
  }

  const handleReplaceTag = async (fromTagId, toTagId) => {
    await replaceTag(fromTagId, toTagId)
    const toTag = tags.find((t) => t.id === toTagId)
    setTags((prev) => prev.filter((t) => t.id !== fromTagId))
    queryClient.setQueryData(CARDS_QUERY_KEY, (prev) =>
      (prev ?? []).map((todo) => {
        const hasFrom = (todo.tags ?? []).some((tg) => tg.id === fromTagId)
        if (!hasFrom) return todo
        const hasTo = (todo.tags ?? []).some((tg) => tg.id === toTagId)
        const filtered = (todo.tags ?? []).filter((tg) => tg.id !== fromTagId)
        return { ...todo, tags: hasTo ? filtered : [...filtered, toTag] }
      }),
    )
  }

  return {
    cards,
    setCards,
    loading,
    cardsRef,
    handleAddCard,
    handleUpdateCard,
    handleDeleteCard,
    handleToggle,
    handleAddTag,
    handleRemoveTag,
    handleArchiveCard,
    handleUnarchiveCard,
    handleUpdateTag,
    handleDeleteTag,
    handleReplaceTag,
  }
}
