import { describe, it, expect } from 'vitest'
import { scoreMatch, findBestMatch } from './completion.js'

describe('scoreMatch', () => {
  it('returns 3 for exact match', () => {
    expect(scoreMatch('meditation', 'meditation')).toBe(3)
  })

  it('is case-insensitive', () => {
    expect(scoreMatch('Meditation', 'meditation')).toBe(3)
    expect(scoreMatch('meditation', 'Meditation')).toBe(3)
    expect(scoreMatch('MEDITATION', 'MEDITATION')).toBe(3)
  })

  it('returns 1 when name contains query', () => {
    expect(scoreMatch('morning meditation', 'meditation')).toBe(1)
  })

  it('returns 1 when query contains name', () => {
    expect(scoreMatch('meditation', 'morning meditation')).toBe(1)
  })

  it('returns 0 for no match', () => {
    expect(scoreMatch('exercise', 'meditation')).toBe(0)
  })

  it('returns 0 for empty query', () => {
    expect(scoreMatch('meditation', '')).toBe(1) // '' is included in any string
  })

  it('exact match beats substring', () => {
    expect(scoreMatch('run', 'run')).toBeGreaterThan(scoreMatch('morning run', 'run'))
  })
})

describe('findBestMatch', () => {
  const habits = [
    { id: 1, name: 'Meditation', archived: false, withings_metric: null },
    { id: 2, name: 'Morning run', archived: false, withings_metric: null },
    { id: 3, name: 'Duolingo Spanish', archived: false, withings_metric: null },
    { id: 4, name: 'Steps', archived: false, withings_metric: 'steps' }, // automatic
    { id: 5, name: 'Old habit', archived: true, withings_metric: null },  // archived
  ]
  const cards = [
    { id: 10, title: 'Call dentist', completed: false, archived: false },
    { id: 11, title: 'Buy groceries', completed: false, archived: false },
    { id: 12, title: 'Done task', completed: true, archived: false },   // completed
    { id: 13, title: 'Old task', completed: false, archived: true },    // archived
  ]

  it('returns null for empty title', () => {
    expect(findBestMatch('', habits, cards)).toBeNull()
    expect(findBestMatch(null, habits, cards)).toBeNull()
  })

  it('returns null when no habits or cards provided', () => {
    expect(findBestMatch('meditation', null, null)).toBeNull()
    expect(findBestMatch('meditation', [], [])).toBeNull()
  })

  it('returns null when nothing matches', () => {
    expect(findBestMatch('xyz123', habits, cards)).toBeNull()
  })

  it('finds exact habit match', () => {
    expect(findBestMatch('Meditation', habits, cards)).toEqual({ kind: 'habit', id: 1 })
  })

  it('finds partial habit match', () => {
    expect(findBestMatch('meditation', habits, cards)).toEqual({ kind: 'habit', id: 1 })
  })

  it('finds habit by substring', () => {
    expect(findBestMatch('run', habits, cards)).toEqual({ kind: 'habit', id: 2 })
  })

  it('finds task match', () => {
    expect(findBestMatch('dentist', habits, cards)).toEqual({ kind: 'task', id: 10 })
  })

  it('finds exact task match', () => {
    expect(findBestMatch('call dentist', habits, cards)).toEqual({ kind: 'task', id: 10 })
  })

  it('excludes archived habits', () => {
    expect(findBestMatch('old habit', habits, cards)).toBeNull()
  })

  it('excludes automatic (withings) habits', () => {
    expect(findBestMatch('steps', habits, cards)).toBeNull()
  })

  it('excludes completed tasks', () => {
    expect(findBestMatch('done task', habits, cards)).toBeNull()
  })

  it('excludes archived tasks', () => {
    expect(findBestMatch('old task', habits, cards)).toBeNull()
  })

  it('prefers exact match over substring match', () => {
    const h = [
      { id: 1, name: 'run', archived: false, withings_metric: null },
      { id: 2, name: 'morning run', archived: false, withings_metric: null },
    ]
    expect(findBestMatch('run', h, [])).toEqual({ kind: 'habit', id: 1 })
  })

  it('habits and tasks both considered — best score wins', () => {
    const h = [{ id: 1, name: 'morning run', archived: false, withings_metric: null }]
    const c = [{ id: 10, title: 'run', completed: false, archived: false }]
    // 'run' exact-matches the task (score 3) vs substring-matches the habit (score 1)
    expect(findBestMatch('run', h, c)).toEqual({ kind: 'task', id: 10 })
  })

  it('habit wins tie-break when both have same score (first in iteration)', () => {
    const h = [{ id: 1, name: 'run', archived: false, withings_metric: null }]
    const c = [{ id: 10, title: 'run', completed: false, archived: false }]
    // Both exact match 'run'; habit is iterated first so wins (score must be strictly greater)
    expect(findBestMatch('run', h, c)).toEqual({ kind: 'habit', id: 1 })
  })

  it('handles undefined habits/cards gracefully', () => {
    expect(findBestMatch('meditation', undefined, undefined)).toBeNull()
  })

  it('matches Duolingo Spanish case-insensitively', () => {
    expect(findBestMatch('duolingo spanish', habits, cards)).toEqual({ kind: 'habit', id: 3 })
    expect(findBestMatch('Duolingo', habits, cards)).toEqual({ kind: 'habit', id: 3 })
  })
})
