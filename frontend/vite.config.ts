/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * S37 §5.2: index.html ships a production-tight CSP whose connect-src is
 * `'self'` only. Dev needs the Vite HMR websocket + the local gateway, so
 * this plugin widens connect-src ONLY in `serve` mode — the bare `ws:`/`wss:`
 * wildcard never reaches a production build. Cross-origin production
 * gateways widen connect-src at deploy time (documented in ENTERPRISE.md).
 */
function devCspPlugin(): Plugin {
  const DEV_CONNECT = "connect-src 'self' http://localhost:8000 ws://localhost:* ws://127.0.0.1:*;"
  return {
    name: 'aura-dev-csp',
    apply: 'serve',
    transformIndexHtml(html) {
      return html.replace("connect-src 'self';", DEV_CONNECT)
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), devCspPlugin()],
  server: {
    port: 5173,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    fakeTimers: { shouldAdvanceTime: true },
  },
})
