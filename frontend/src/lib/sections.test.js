import { describe, it, expect } from 'vitest'
import { SECTIONS, SECTION_LABELS, ALL_SECTIONS } from './sections'

describe('SECTIONS', () => {
  it('contains exactly the four board sections in display order', () => {
    expect(SECTIONS).toEqual(['today', 'week', 'month', 'later'])
  })

  it('does not include "none"', () => {
    expect(SECTIONS).not.toContain('none')
  })
})

describe('SECTION_LABELS', () => {
  it('maps all four board sections', () => {
    expect(SECTION_LABELS.today).toBe('Today')
    expect(SECTION_LABELS.week).toBe('This Week')
    expect(SECTION_LABELS.month).toBe('This Month')
    expect(SECTION_LABELS.later).toBe('Later')
  })

  it('has a label for every SECTIONS entry', () => {
    for (const s of SECTIONS) {
      expect(SECTION_LABELS[s]).toBeTruthy()
    }
  })
})

describe('ALL_SECTIONS', () => {
  it('contains exactly four entries', () => {
    expect(ALL_SECTIONS).toHaveLength(4)
    const values = ALL_SECTIONS.map((s) => s.value)
    expect(values).not.toContain('none')
  })

  it('each entry has a non-empty value and label', () => {
    for (const s of ALL_SECTIONS) {
      expect(s.value).toBeTruthy()
      expect(s.label).toBeTruthy()
    }
  })

  it('"later" is labelled "Later"', () => {
    const later = ALL_SECTIONS.find((s) => s.value === 'later')
    expect(later?.label).toBe('Later')
  })

  it('values are consistent with SECTIONS', () => {
    const allValues = ALL_SECTIONS.map((s) => s.value)
    expect(allValues).toEqual(SECTIONS)
  })
})
