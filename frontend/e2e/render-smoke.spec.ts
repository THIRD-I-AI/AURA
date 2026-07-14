import { test, expect, type Page } from '@playwright/test';

/**
 * Render smoke for the terminal command decks. Runs against the built app
 * served by `vite preview` with NO backend, so every panel must degrade to an
 * honest empty / offline / awaiting-data state rather than crash or render to
 * a zero-size box.
 *
 * The auth gate (ProtectedRoute) bounces anonymous visitors to /login, so we
 * seed a decodable, non-expired JWT-shaped token under the storage key the app
 * reads (`aura.authToken`). decodeAuthToken only inspects the claims segment
 * and the `exp` field, so an unsigned token with a far-future exp is enough.
 */
const TOKEN_KEY = 'aura.authToken';

function seedToken(): string {
  const claims = {
    sub: 'e2e-smoke',
    name: 'E2E Smoke',
    org_id: 'org-e2e',
    exp: Math.floor(Date.now() / 1000) + 3600,
  };
  const b64 = Buffer.from(JSON.stringify(claims)).toString('base64');
  return `h.${b64}.s`;
}

async function gotoAuthed(page: Page, path: string): Promise<void> {
  const token = seedToken();
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [TOKEN_KEY, token],
  );
  await page.goto(path);
}

test.describe('pipeline command deck renders with real layout', () => {
  test('DAG SVG has a non-zero, unclipped bounding box', async ({ page }) => {
    await gotoAuthed(page, '/app/terminal?panel=pipeline');

    const panel = page.getByTestId('pipeline-panel');
    await expect(panel).toBeVisible();

    const svg = page.locator('svg.pl-graph');
    await expect(svg).toBeVisible();

    // THE invisible-DAG catch: jsdom reports 0x0; real Chromium does not.
    const box = await svg.boundingBox();
    expect(box, 'DAG SVG must have a layout box').not.toBeNull();
    expect(box!.width).toBeGreaterThan(50);
    expect(box!.height).toBeGreaterThan(50);

    // The SVG must fit inside its scroll container, not overflow into a
    // clipped fixed-px canvas (the original 1280x632 in a ~660px box).
    const wrap = page.locator('.pl-graph-wrap');
    const wrapBox = await wrap.boundingBox();
    expect(wrapBox).not.toBeNull();
    expect(box!.width).toBeLessThanOrEqual(wrapBox!.width + 2);
    expect(box!.height).toBeLessThanOrEqual(wrapBox!.height + 2);
  });

  test('status glyphs are honest: monitored-but-offline services never read as unmonitored', async ({ page }) => {
    await gotoAuthed(page, '/app/terminal?panel=pipeline');
    await expect(page.getByTestId('pipeline-panel')).toBeVisible();

    // With no backend, monitored services must show the 'unknown' glyph (◌),
    // never the 'unmonitored' one (·). At least one 'unknown' must appear.
    const glyphs = await page.locator('text.pl-node-glyph').allTextContents();
    expect(glyphs.length).toBeGreaterThan(0);
    expect(glyphs).toContain('◌');
  });
});
