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

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: `npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
