import { describe, it, expect } from 'vitest'
import descriptionToHtml from './descriptionToHtml'

describe('descriptionToHtml', () => {
  // ── Empty / null input ────────────────────────────────────────────────────

  it('returns empty string for null', () => {
    expect(descriptionToHtml(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(descriptionToHtml(undefined)).toBe('')
  })

  it('returns empty string for empty string', () => {
    expect(descriptionToHtml('')).toBe('')
  })

  // ── Plain text — HTML escaping ────────────────────────────────────────────

  it('escapes < and > in plain text', () => {
    const result = descriptionToHtml('a < b > c')
    expect(result).toContain('&lt;')
    expect(result).toContain('&gt;')
    expect(result).not.toContain('<b>')
  })

  it('escapes & in plain text', () => {
    const result = descriptionToHtml('cats & dogs')
    expect(result).toContain('&amp;')
  })

  it('converts newlines to <br>', () => {
    const result = descriptionToHtml('line one\nline two')
    expect(result).toContain('<br>')
  })

  // ── URL linkification in plain text ──────────────────────────────────────

  it('turns a bare http URL into an anchor', () => {
    const result = descriptionToHtml('visit http://example.com for info')
    expect(result).toContain('<a ')
    expect(result).toContain('href="http://example.com"')
    expect(result).toContain('target="_blank"')
    expect(result).toContain('rel="noopener noreferrer"')
  })

  it('turns a bare https URL into an anchor', () => {
    const result = descriptionToHtml('see https://example.com/page')
    expect(result).toContain('href="https://example.com/page"')
  })

  it('does not linkify non-URL text', () => {
    const result = descriptionToHtml('just plain text here')
    expect(result).not.toContain('<a ')
  })

  // ── HTML passthrough — DOMPurify sanitization ─────────────────────────────

  it('passes through safe HTML unchanged', () => {
    const html = '<p>Hello <strong>world</strong></p>'
    const result = descriptionToHtml(html)
    expect(result).toContain('<p>')
    expect(result).toContain('<strong>world</strong>')
  })

  it('adds target=_blank and rel to links in HTML input', () => {
    const html = '<a href="https://example.com">click</a>'
    const result = descriptionToHtml(html)
    expect(result).toContain('target="_blank"')
    expect(result).toContain('rel="noopener noreferrer"')
  })

  it('strips script tags from HTML input', () => {
    const html = '<p>safe</p><script>alert(1)</script>'
    const result = descriptionToHtml(html)
    expect(result).not.toContain('<script>')
    expect(result).not.toContain('alert(1)')
    expect(result).toContain('<p>safe</p>')
  })

  it('strips onclick handlers from HTML input', () => {
    const html = '<a href="https://ok.com" onclick="evil()">click</a>'
    const result = descriptionToHtml(html)
    expect(result).not.toContain('onclick')
  })

  it('strips javascript: hrefs from HTML input', () => {
    const html = '<a href="javascript:alert(1)">click</a>'
    const result = descriptionToHtml(html)
    expect(result).not.toContain('javascript:')
  })
})
