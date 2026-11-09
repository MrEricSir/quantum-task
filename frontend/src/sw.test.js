import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import path from 'path'

// Regression test for a real incident: vite-plugin-pwa's default `generateSW` strategy
// generates its OWN sw.js from scratch at build time and overwrites this file's output --
// the push/notificationclick handlers below shipped to dist/ for a while with zero effect,
// since the service worker that actually ran had no code to receive a push at all. Can't
// actually import this file (it's a service worker -- no `self.__WB_MANIFEST`/ServiceWorker
// globals in a jsdom test env), so this checks the source text directly: as long as these
// pieces stay present, and vite.config.js stays on `injectManifest` (checked in the sibling
// vite.config test below), this file's own build output keeps including them.

const swSource = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), 'sw.js'),
  'utf8',
)

const viteConfigSource = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'vite.config.js'),
  'utf8',
)

describe('src/sw.js', () => {
  it('precaches the Workbox-injected manifest', () => {
    expect(swSource).toMatch(/precacheAndRoute\(self\.__WB_MANIFEST\)/)
  })

  it('still handles push notifications', () => {
    expect(swSource).toMatch(/addEventListener\(\s*['"]push['"]/)
  })

  it('still handles notification clicks', () => {
    expect(swSource).toMatch(/addEventListener\(\s*['"]notificationclick['"]/)
  })

  it('still coalesces in-flight requests via lib/requestCoalescing (behavior tested there)', () => {
    expect(swSource).toMatch(/from ['"]\.\/lib\/requestCoalescing['"]/)
    expect(swSource).toMatch(/shouldCoalesce\(/)
  })
})

describe('vite.config.js PWA strategy', () => {
  it('uses injectManifest, not the default generateSW, so src/sw.js ships as written', () => {
    expect(viteConfigSource).toMatch(/strategies:\s*['"]injectManifest['"]/)
  })

  it('points the PWA plugin at src/sw.js', () => {
    expect(viteConfigSource).toMatch(/srcDir:\s*['"]src['"]/)
    expect(viteConfigSource).toMatch(/filename:\s*['"]sw\.js['"]/)
  })
})
