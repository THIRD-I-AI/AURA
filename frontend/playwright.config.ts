import { defineConfig, devices } from '@playwright/test';

/**
 * Render smoke harness for the terminal command decks.
 *
 * WHY THIS EXISTS: vitest + jsdom does no layout, so it reports every element
 * as 0x0. That is exactly how the "invisible DAG" bug (an SVG emitted at a
 * fixed 1280x632 px inside a ~660 px overflow:auto box) shipped past a green
 * unit suite. Playwright drives a real Chromium with real layout, so a panel
 * that renders to a zero-size or clipped box fails here.
 *
 * The harness serves the *built* app with `vite preview` (no backend), which
 * is enough for a render smoke: every panel must mount and show its honest
 * empty / offline / awaiting-data state when the API is unreachable.
 */
const PORT = 4173;

/**
 * E2E_BASE_URL points the suite at an already-running deployment instead of a
 * locally-previewed build:
 *
 *   E2E_BASE_URL=https://your-host npx playwright test
 *
 * This matters because the localhost harness serves the built frontend with NO
 * backend, so it can only ever assert honest empty/offline states. It cannot
 * catch anything depending on the real API — and the first real deployment
 * produced a run of bugs invisible to every green suite (CORS unreadable from
 * the environment, users wiped on restart, connector endpoints 500ing, a
 * connector silently reading :memory:). Every one needed a live server.
 *
 * With a remote target the local preview server is NOT started: the deployment
 * under test IS the server, and spawning a second one would waste time and
 * silently shadow the target for any spec using a relative URL.
 */
const REMOTE_BASE_URL = process.env.E2E_BASE_URL;
const baseURL = REMOTE_BASE_URL || `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  ...(REMOTE_BASE_URL
    ? {}
    : {
        webServer: {
          command: `npm run preview -- --port ${PORT} --strictPort`,
          url: `http://localhost:${PORT}`,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
      }),
});
