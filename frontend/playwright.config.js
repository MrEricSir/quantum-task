import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  retries: 0,
  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }]]
    : [['html', { open: 'never' }]],
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'on-first-retry',
    // The app registers a real service worker (src/sw.js) that now actively intercepts
    // fetch() for several /api/ paths (in-flight request coalescing, see
    // src/lib/requestCoalescing.js) -- against a real backend that's exactly the point, but
    // in these tests it means the SW's OWN fetch() races page.route()'s mocks instead of
    // going through them, since a service-worker-initiated fetch isn't routed the same way
    // as the page's. Blocking SW registration here keeps these tests testing the app's
    // client-side behavior against mocked APIs; the SW's own logic is covered directly by
    // src/lib/requestCoalescing.test.js and src/sw.test.js instead.
    serviceWorkers: 'block',
    ...devices['Desktop Chrome'],
  },
  expect: {
    // Allow minor sub-pixel / emoji rendering differences (≤ 0.1% of pixels)
    toHaveScreenshot: { maxDiffPixelRatio: 0.001 },
  },
  // Build once then preview — faster and production-faithful
  webServer: {
    command: 'npm run build && npm run preview',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
