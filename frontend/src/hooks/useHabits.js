import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchHabits,
  fetchArchivedHabits,
  createHabit,
  updateHabit,
  deleteHabit,
  checkHabit,
  uncheckHabit,
  archiveHabit,
  unarchiveHabit,
} from '../api'

export const HABITS_QUERY_KEY = ['habits']
export const ARCHIVED_HABITS_QUERY_KEY = ['habits', 'archived']

export function useHabits({ authed, invalidateBriefing }) {
  const queryClient = useQueryClient()

  const { data: habits = [] } = useQuery({
    queryKey: HABITS_QUERY_KEY,
    queryFn: fetchHabits,
    enabled: !!authed,
  })

  const { data: archivedHabits = [] } = useQuery({
    queryKey: ARCHIVED_HABITS_QUERY_KEY,
    queryFn: fetchArchivedHabits,
    enabled: !!authed,
  })

  const setHabits = useCallback(
    (updater) => queryClient.setQueryData(HABITS_QUERY_KEY, updater),
    [queryClient],
  )

  const setArchivedHabits = useCallback(
    (updater) => queryClient.setQueryData(ARCHIVED_HABITS_QUERY_KEY, updater),
    [queryClient],
  )

  const handleAddHabit = async (data) => {
    const habit = await createHabit(data)
    queryClient.setQueryData(HABITS_QUERY_KEY, (prev) => [...(prev ?? []), habit])
    return habit
  }

  const handleUpdateHabit = async (id, data) => {
    const updated = await updateHabit(id, data)
    queryClient.setQueryData(HABITS_QUERY_KEY, (prev) =>
      (prev ?? []).map((h) => (h.id === id ? updated : h)),
    )
    return updated
  }

  const handleDeleteHabit = async (id) => {
    queryClient.setQueryData(HABITS_QUERY_KEY, (prev) =>
      (prev ?? []).filter((h) => h.id !== id),
    )
    queryClient.setQueryData(ARCHIVED_HABITS_QUERY_KEY, (prev) =>
      (prev ?? []).filter((h) => h.id !== id),
    )
    await deleteHabit(id)
  }

  const handleArchiveHabit = async (id) => {
    await archiveHabit(id)
    const habit = habits.find((h) => h.id === id)
    queryClient.setQueryData(HABITS_QUERY_KEY, (prev) =>
      (prev ?? []).filter((h) => h.id !== id),
    )
    if (habit) {
      queryClient.setQueryData(ARCHIVED_HABITS_QUERY_KEY, (prev) => [
        ...(prev ?? []),
        { ...habit, archived: true },
      ])
    }
  }

  const handleUnarchiveHabit = async (id) => {
    await unarchiveHabit(id)
    const habit = archivedHabits.find((h) => h.id === id)
    queryClient.setQueryData(ARCHIVED_HABITS_QUERY_KEY, (prev) =>
      (prev ?? []).filter((h) => h.id !== id),
    )
    if (habit) {
      queryClient.setQueryData(HABITS_QUERY_KEY, (prev) => [
        ...(prev ?? []),
        { ...habit, archived: false },
      ])
    }
  }

  const handleToggleHabit = async (habit) => {
    const wasChecked = habit.completed_today
    queryClient.setQueryData(HABITS_QUERY_KEY, (prev) =>
      (prev ?? []).map((h) =>
        h.id === habit.id
          ? {
              ...h,
              completed_today: !wasChecked,
              streak: !wasChecked ? h.streak + 1 : Math.max(0, h.streak - 1),
            }
          : h,
      ),
    )
    try {
      if (wasChecked) {
        await uncheckHabit(habit.id)
      } else {
        await checkHabit(habit.id)
      }
      const updated = await fetchHabits()
      queryClient.setQueryData(HABITS_QUERY_KEY, updated)
      invalidateBriefing?.()
    } catch {
      queryClient.setQueryData(HABITS_QUERY_KEY, (prev) =>
        (prev ?? []).map((h) => (h.id === habit.id ? habit : h)),
      )
    }
  }

  return {
    habits,
    setHabits,
    archivedHabits,
    setArchivedHabits,
    handleAddHabit,
    handleUpdateHabit,
    handleDeleteHabit,
    handleArchiveHabit,
    handleUnarchiveHabit,
    handleToggleHabit,
  }
}
