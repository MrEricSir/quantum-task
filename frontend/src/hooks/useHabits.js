import { useState, useEffect } from 'react'
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

export function useHabits({ authed, invalidateBriefing }) {
  const [habits, setHabits] = useState([])
  const [archivedHabits, setArchivedHabits] = useState([])

  useEffect(() => {
    if (!authed) return
    fetchHabits().then(setHabits).catch(() => {})
    fetchArchivedHabits().then(setArchivedHabits).catch(() => {})
  }, [authed])

  const handleAddHabit = async (data) => {
    const habit = await createHabit(data)
    setHabits((prev) => [...prev, habit])
    return habit
  }

  const handleUpdateHabit = async (id, data) => {
    const updated = await updateHabit(id, data)
    setHabits((prev) => prev.map((h) => (h.id === id ? updated : h)))
    return updated
  }

  const handleDeleteHabit = async (id) => {
    setHabits((prev) => prev.filter((h) => h.id !== id))
    setArchivedHabits((prev) => prev.filter((h) => h.id !== id))
    await deleteHabit(id)
  }

  const handleArchiveHabit = async (id) => {
    await archiveHabit(id)
    const habit = habits.find((h) => h.id === id)
    setHabits((prev) => prev.filter((h) => h.id !== id))
    if (habit) setArchivedHabits((prev) => [...prev, { ...habit, archived: true }])
  }

  const handleUnarchiveHabit = async (id) => {
    await unarchiveHabit(id)
    const habit = archivedHabits.find((h) => h.id === id)
    setArchivedHabits((prev) => prev.filter((h) => h.id !== id))
    if (habit) setHabits((prev) => [...prev, { ...habit, archived: false }])
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
      invalidateBriefing?.()
    } catch {
      setHabits((prev) => prev.map((h) => (h.id === habit.id ? habit : h)))
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
