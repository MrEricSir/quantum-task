import { useState } from 'react'

// Card creation/editing on mobile always goes through CardSheet (a bottom
// sheet); this hook only manages the "new card" case (openNewCard), since
// App.jsx's own openEdit routes editing an existing card straight to
// CardSheet (mobile) or CardDetailPanel (desktop) without needing state
// from here.
export function useModals() {
  const [showQuickAdd, setShowQuickAdd] = useState(false)
  const [quickAddInitialText, setQuickAddInitialText] = useState('')
  const [showSearch, setShowSearch] = useState(false)
  const [showTagManager, setShowTagManager] = useState(false)
  const [showCalendarSettings, setShowCalendarSettings] = useState(false)
  const [showGithubSettings, setShowGithubSettings] = useState(false)
  const [showWithingsSettings, setShowWithingsSettings] = useState(false)
  const [showTelegramSettings, setShowTelegramSettings] = useState(false)
  const [showNavSettings, setShowNavSettings] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [defaultSection, setDefaultSection] = useState('today')
  const [showNewSheet, setShowNewSheet] = useState(false)

  // Only ever called from App.jsx's own openNewCard after it has already
  // confirmed the viewport is mobile-sized.
  const openNewCard = (section = 'today') => {
    setDefaultSection(section)
    setShowNewSheet(true)
  }

  return {
    showQuickAdd, setShowQuickAdd,
    quickAddInitialText, setQuickAddInitialText,
    showSearch, setShowSearch,
    showTagManager, setShowTagManager,
    showCalendarSettings, setShowCalendarSettings,
    showGithubSettings, setShowGithubSettings,
    showWithingsSettings, setShowWithingsSettings,
    showTelegramSettings, setShowTelegramSettings,
    showNavSettings, setShowNavSettings,
    showShortcuts, setShowShortcuts,
    defaultSection,
    showNewSheet, setShowNewSheet,
    openNewCard,
  }
}
