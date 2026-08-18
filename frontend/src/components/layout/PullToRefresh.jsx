import { useEffect, useRef, useState } from 'react'
import './PullToRefresh.css'

// iOS standalone PWAs have no native pull-to-refresh (the manifest's
// display: standalone suppresses it), and overscroll-behavior is
// deliberately disabled everywhere else in this app (see index.css) so
// there's no native bounce to repurpose either -- this builds the whole
// gesture (indicator, resistance, threshold) from touch events.
const PULL_THRESHOLD = 70
const MAX_PULL = 120
const RESISTANCE = 0.5

export default function PullToRefresh({ children, onRefresh, className = '' }) {
  const scrollRef = useRef(null)
  const trackingRef = useRef(false)
  const startYRef = useRef(0)
  const [pullDistance, setPullDistance] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    const handleTouchStart = (e) => {
      if (refreshing) return
      // Only track as a pull-to-refresh gesture if it starts at the very
      // top of the scroll container -- otherwise this is an ordinary scroll.
      if (el.scrollTop === 0) {
        trackingRef.current = true
        startYRef.current = e.touches[0].clientY
        setDragging(true)
      }
    }

    const handleTouchMove = (e) => {
      if (!trackingRef.current) return
      const deltaY = e.touches[0].clientY - startYRef.current
      if (deltaY <= 0) {
        setPullDistance(0)
        return
      }
      // Real synthetic React onTouchMove handlers can be attached passively
      // in some browsers, silently no-oping preventDefault -- this listener
      // is added natively with { passive: false } below specifically so
      // this call actually stops the page from also trying to scroll.
      e.preventDefault()
      setPullDistance(Math.min(deltaY * RESISTANCE, MAX_PULL))
    }

    const handleTouchEnd = () => {
      if (!trackingRef.current) return
      trackingRef.current = false
      setDragging(false)
      setPullDistance((current) => {
        if (current >= PULL_THRESHOLD) {
          setRefreshing(true)
          Promise.resolve(onRefresh?.()).finally(() => {
            setRefreshing(false)
            setPullDistance(0)
          })
          return current
        }
        return 0
      })
    }

    el.addEventListener('touchstart', handleTouchStart, { passive: true })
    el.addEventListener('touchmove', handleTouchMove, { passive: false })
    el.addEventListener('touchend', handleTouchEnd, { passive: true })
    return () => {
      el.removeEventListener('touchstart', handleTouchStart)
      el.removeEventListener('touchmove', handleTouchMove)
      el.removeEventListener('touchend', handleTouchEnd)
    }
  }, [onRefresh, refreshing])

  const indicatorHeight = refreshing ? PULL_THRESHOLD : pullDistance
  const ready = refreshing || pullDistance >= PULL_THRESHOLD

  return (
    <main ref={scrollRef} className={className}>
      <div
        className={`pull-to-refresh-indicator${dragging ? '' : ' pull-to-refresh-indicator--settling'}`}
        style={{ height: indicatorHeight, opacity: indicatorHeight > 0 ? 1 : 0 }}
      >
        <span className={`pull-to-refresh-spinner${ready ? ' pull-to-refresh-spinner--active' : ''}`} />
      </div>
      {children}
    </main>
  )
}
