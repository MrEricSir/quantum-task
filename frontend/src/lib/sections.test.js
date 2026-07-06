import { describe, it, expect } from 'vitest'
import { SECTIONS, SECTION_LABELS, ALL_SECTIONS } from './sections'

describe('SECTIONS', () => {
  it('contains exactly the four board sections in display order', () => {
    expect(SECTIONS).toEqual(['today', 'week', 'month', 'later'])
  })

  it('does not include "none" (reference cards are not a board section)', () => {
    expect(SECTIONS).not.toContain('none')
  })
})

describe('SECTION_LABELS', () => {
  it('maps all four board sections', () => {
    expect(SECTION_LABELS.today).toBe('Today')
    expect(SECTION_LABELS.week).toBe('This Week')
    expect(SECTION_LABELS.month).toBe('This Month')
    expect(SECTION_LABELS.later).toBe('Stash')
  })

  it('maps "none" for use in search/badge contexts', () => {
    expect(SECTION_LABELS.none).toBe('Card')
  })

  it('has a label for every SECTIONS entry', () => {
    for (const s of SECTIONS) {
      expect(SECTION_LABELS[s]).toBeTruthy()
    }
  })
})

describe('ALL_SECTIONS', () => {
  it('contains five entries including "none"', () => {
    expect(ALL_SECTIONS).toHaveLength(5)
    const values = ALL_SECTIONS.map((s) => s.value)
    expect(values).toContain('none')
  })

  it('each entry has a non-empty value and label', () => {
    for (const s of ALL_SECTIONS) {
      expect(s.value).toBeTruthy()
      expect(s.label).toBeTruthy()
    }
  })

  it('board sections appear before "none"', () => {
    const values = ALL_SECTIONS.map((s) => s.value)
    const noneIndex = values.indexOf('none')
    expect(noneIndex).toBe(values.length - 1)
  })

  it('"later" is labelled "Stash"', () => {
    const later = ALL_SECTIONS.find((s) => s.value === 'later')
    expect(later?.label).toBe('Stash')
  })

  it('values are consistent with SECTIONS (excluding none)', () => {
    const allValues = ALL_SECTIONS.map((s) => s.value).filter((v) => v !== 'none')
    expect(allValues).toEqual(SECTIONS)
  })
})
