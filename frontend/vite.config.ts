/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite'
import { configDefaults } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

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
  // NOTE (rolldown-vite + Tailwind v4): Vite 8 ships Lightning CSS and Tailwind
  // v4's oxide engine also uses it. We deliberately keep Vite's default CSS
  // minifier (we do NOT set css.transformer:'lightningcss') to avoid a
  // double-optimize path through Lightning CSS; the default build is verified
  // clean, so no extra Tailwind config is needed.
  plugins: [tailwindcss(), react(), devCspPlugin()],
  server: {
    port: 5173,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    fakeTimers: { shouldAdvanceTime: true },
    // Vitest's default include is **/*.{test,spec}.*, which sweeps in the
    // Playwright e2e specs under e2e/. Those import from '@playwright/test'
    // and call test.describe() at module load, which throws under the Vitest
    // runner ("did not expect test.describe() to be called here"). Playwright
    // owns e2e/ (see playwright.config.ts testDir); Vitest must not collect it.
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
