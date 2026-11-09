import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import path from 'path'

// Regression test for the iOS 27 standalone-PWA top-blur workaround in index.html. There's
// no WebKit engine in this project's test suite (Playwright here runs Chromium, which never
// matches the @supports(-webkit-touch-callout) gate anyway), so this checks the static
// HTML/CSS contract rather than actual rendering -- same approach as src/sw.test.js for the
// service worker. See index.html's own comment for the mechanism and the originating PR.

const html = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'index.html'),
  'utf8',
)

describe('iOS 27 standalone status-bar blur workaround', () => {
  it('places an empty, non-interactive marker div right after <body>, outside #root', () => {
    const bodyOpenIndex = html.indexOf('<body>')
    const markerIndex = html.indexOf('class="ios-status-bar-fix"')
    const rootIndex = html.indexOf('id="root"')
    expect(bodyOpenIndex).toBeGreaterThan(-1)
    expect(markerIndex).toBeGreaterThan(bodyOpenIndex)
    expect(markerIndex).toBeLessThan(rootIndex)
    expect(html).toMatch(/<div class="ios-status-bar-fix" aria-hidden="true"><\/div>/)
  })

  it('is disabled by default (display: none) outside any @supports/@media gating', () => {
    expect(html).toMatch(/\.ios-status-bar-fix\s*\{\s*display:\s*none;\s*\}/)
  })

  it('only enables in installed standalone mode on engines with text background-clip support', () => {
    expect(html).toMatch(
      /@supports \(-webkit-touch-callout: none\) and \(background-clip: text\)/,
    )
    expect(html).toMatch(/@media \(display-mode: standalone\)/)
  })

  it('keeps the fixed, >10px-tall, non-interactive, text-clipped CSS contract', () => {
    const match = html.match(/\.ios-status-bar-fix\s*\{([^}]*)\}\s*\}\s*\}/)
    expect(match).not.toBeNull()
    const body = match[1]
    expect(body).toMatch(/display:\s*block;/)
    expect(body).toMatch(/position:\s*fixed;/)
    expect(body).toMatch(/height:\s*11px;/)
    expect(body).toMatch(/pointer-events:\s*none;/)
    expect(body).toMatch(/background-clip:\s*text;/)
    // Never make this invisible via opacity/visibility -- that removes the solid background
    // WebKit's sampler is looking for, defeating the whole workaround (see index.html comment).
    expect(body).not.toMatch(/opacity:\s*0/)
    expect(body).not.toMatch(/visibility:\s*hidden/)
  })
})
