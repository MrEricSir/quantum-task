import { useState } from 'react'

export function useModals() {
  const [showModal, setShowModal] = useState(false)
  const [showQuickAdd, setShowQuickAdd] = useState(false)
  const [quickAddInitialText, setQuickAddInitialText] = useState('')
  const [showSearch, setShowSearch] = useState(false)
  const [showTagManager, setShowTagManager] = useState(false)
  const [showCalendarSettings, setShowCalendarSettings] = useState(false)
  const [showGithubSettings, setShowGithubSettings] = useState(false)
  const [showWithingsSettings, setShowWithingsSettings] = useState(false)
  const [showTelegramSettings, setShowTelegramSettings] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [editingCard, setEditingCard] = useState(null)
  const [defaultSection, setDefaultSection] = useState('today')
  const [showNewSheet, setShowNewSheet] = useState(false)

  const openEdit = (todo) => {
    setDefaultSection(todo?.section ?? 'today')
    setEditingCard(todo)
    setShowModal(true)
  }

  const openNewCard = (section = 'today') => {
    setDefaultSection(section)
    if (window.matchMedia('(max-width: 640px)').matches) {
      setShowNewSheet(true)
    } else {
      setEditingCard(null)
      setShowModal(true)
    }
  }

  const closeModal = () => {
    setShowModal(false)
    setEditingCard(null)
  }

  return {
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
    defaultSection,
    showNewSheet, setShowNewSheet,
    openEdit, openNewCard, closeModal,
  }
}
