import { useEffect, useState } from 'react'

// Default stages assume the slowness is the Cloud Run backend cold-starting
// (min-instances is 0, so the container can be fully stopped between uses) --
// see README's Deployment section. The empty string for the first stage means
// "no extra text yet" so a normal, fast load never shows anything beyond the
// caller's own spinner.
export const DEFAULT_LOADING_STAGES = [
  { after: 0, text: '' },
  { after: 4000, text: 'Waking up the server…' },
  { after: 15000, text: 'First load after inactivity can take a bit longer…' },
]

// Returns the text for the latest stage whose `after` (ms) has elapsed since
// `active` most recently became true. Resets to the first stage's text every
// time `active` toggles back to true, so a retried/reattempted wait starts
// over rather than instantly showing the most escalated message.
export default function useLoadingStage(active, stages = DEFAULT_LOADING_STAGES) {
  const [text, setText] = useState(stages[0].text)

  useEffect(() => {
    if (!active) {
      setText(stages[0].text)
      return
    }
    setText(stages[0].text)
    const timers = stages
      .filter((s) => s.after > 0)
      .map((s) => setTimeout(() => setText(s.text), s.after))
    return () => timers.forEach(clearTimeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active])

  return text
}
