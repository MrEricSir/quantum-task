import { useEffect, useState } from 'react'

// Shared "Extract action items" trigger state for a card, used by both
// CardDetailPanel (desktop) and CardSheet (mobile) -- previously each
// duplicated this state and handler independently, which is how
// CardDetailPanel's own "reset when a different card is opened" effect
// ended up forgetting to clear extracting/extractError (a real bug: a
// failed extraction on one card stayed visible after switching to another
// card without closing the panel). Resetting here, keyed on the card's own
// id, means no separate host component has to remember to do it.
export function useExtractActions(card, onExtractActions) {
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState('')

  useEffect(() => {
    setExtracting(false)
    setExtractError('')
  }, [card?.id])

  const handleExtractActions = async () => {
    if (!card?.id || extracting || !onExtractActions) return
    setExtracting(true)
    setExtractError('')
    try {
      await onExtractActions(card)
    } catch (e) {
      setExtractError(e.message || 'Failed to extract action items')
    } finally {
      setExtracting(false)
    }
  }

  return { extracting, extractError, handleExtractActions }
}
