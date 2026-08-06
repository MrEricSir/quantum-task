import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.js', 'src/**/*.test.jsx'],
  },
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'Quantum Task',
        short_name: 'QuantumTask',
        description: 'Personal task and reminder dashboard',
        theme_color: '#07040f',
        background_color: '#07040f',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any maskable' },
          { src: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' },
        ],
      },
      workbox: {
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//],
        globPatterns: ['**/*.{js,css,html,svg,png}'],
        runtimeCaching: [
          {
            urlPattern: /^\/api\//,
            handler: 'NetworkOnly',
          },
        ],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  server: {
    proxy: {
      // Backend port is normally 8000, but a qtask-bridge worktree running
      // this app via Procfile.dev reserves its own port range and passes
      // QTASK_PORT_BASE through as the backend's actual port (see
      // Procfile.dev) -- proxy there instead so `--run` doesn't silently
      // talk to whatever's on 8000 (likely a different worktree or your
      // main dev instance).
      '/api': `http://localhost:${process.env.QTASK_PORT_BASE || 8000}`,
    },
  },
})
