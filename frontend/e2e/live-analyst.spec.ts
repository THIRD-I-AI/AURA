import { test, expect, type Page } from '@playwright/test';

/**
 * Authenticated end-to-end coverage against a LIVE deployment.
 *
 * WHY THIS EXISTS: render-smoke.spec.ts serves the built frontend with no
 * backend and seeds an unsigned token, so it can only assert honest empty
 * states. That is a real gap, not a small one — the first production deploy
 * produced six bugs and every single one was invisible to both the 2038-test
 * backend suite and the render smoke, because each needed a real server,
 * filesystem, or container to surface:
 *
 *   1. CORS unreadable from the environment  → gateway could not boot at all
 *   2. signing key dir unwritable            → refused to start in production
 *   3. evolution engine cast crash           → self-improvement never ran
 *   4. METADATA_DATABASE_URL unset           → user accounts wiped on restart
 *   5. ConnectorConfig kwarg collision       → every connector write 500'd
 *   6. DuckDB ignoring `database`            → silently queried :memory:
 *
 * Bugs 4 and 6 are the ones a suite like this catches directly: #4 only shows
 * up when you log in AFTER a restart, and #6 only shows up when you check that
 * a query returns the RIGHT answer rather than merely returning 200.
 *
 * So these specs run only against a real deployment and assert recovered
 * ground truth, not just HTTP success:
 *
 *   E2E_BASE_URL=https://your-host \
 *   E2E_EMAIL=you@example.com E2E_PASSWORD='…' \
 *   npx playwright test live-analyst
 */
const BASE = process.env.E2E_BASE_URL;
const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

const TOKEN_KEY = 'aura.authToken';

/**
 * A dataset whose answers are computed here, in the test, from the same rows
 * that get uploaded. Hardcoding an expected total would let a wrong constant
 * and a wrong query agree with each other; deriving it means the assertion
 * fails if EITHER side drifts.
 *
 * Values are integers so the assertion never has to reason about float
 * formatting, and large/irregular enough that the expected sum cannot appear
 * in the page by coincidence (a row count or a timestamp will not collide
 * with 1413277).
 */
const ROWS = [
  { region: 'north', revenue: 412337 },
  { region: 'south', revenue: 208115 },
  { region: 'east', revenue: 533902 },
  { region: 'west', revenue: 258923 },
];
const TOTAL_REVENUE = ROWS.reduce((s, r) => s + r.revenue, 0);
const TOP_REGION = ROWS.reduce((a, b) => (b.revenue > a.revenue ? b : a)).region;

/** Fixed name so re-runs overwrite rather than pile up datasets on the box —
 *  and so the question can name the table, keeping the generated SQL aimed at
 *  this data even when a workspace holds other uploads. */
const DATASET = 'e2e_ground_truth.csv';
const TABLE = 'e2e_ground_truth';

const CSV = ['region,revenue', ...ROWS.map((r) => `${r.region},${r.revenue}`)].join('\n');

/** The LLM round trip (generate SQL → cross-check → execute) is slow on a
 *  1 GB box; these are deliberately generous so a slow answer reads as slow
 *  rather than as a failure. */
const ANSWER_TIMEOUT = 180_000;

test.describe('live deployment — authenticated analyst loop', () => {
  test.skip(
    !BASE || !EMAIL || !PASSWORD,
    'Needs a real deployment: set E2E_BASE_URL, E2E_EMAIL and E2E_PASSWORD.',
  );

  test('real credentials log in and land on the workbench', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByTestId('auth-form')).toBeVisible();

    await page.getByTestId('auth-email').fill(EMAIL!);
    await page.getByTestId('auth-password').fill(PASSWORD!);
    await page.getByRole('button', { name: 'Sign in' }).click();

    // Bug #4 lived exactly here: the account existed at registration and was
    // silently gone after the next container restart, so a green login on a
    // FRESH deployment proves the user survived a restart.
    await expect(page.getByTestId('wb-app')).toBeVisible({ timeout: 60_000 });
    await expect(page).toHaveURL(/\/workbench/);

    const token = await page.evaluate((k) => window.localStorage.getItem(k), TOKEN_KEY);
    expect(token, 'a real signed token must be stored after login').toBeTruthy();
    expect(token!.split('.')).toHaveLength(3);
  });

  test('a wrong password is refused and says so', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('auth-email').fill(EMAIL!);
    await page.getByTestId('auth-password').fill('definitely-not-the-password');
    await page.getByRole('button', { name: 'Sign in' }).click();

    await expect(page.getByTestId('auth-error')).toBeVisible({ timeout: 30_000 });
    // Failing closed matters more than the wording: a bad password must never
    // reach the app shell.
    await expect(page.getByTestId('wb-app')).toHaveCount(0);
  });

  test('upload a CSV and get the arithmetically correct answer back', async ({ page }) => {
    test.setTimeout(ANSWER_TIMEOUT + 120_000);

    await loginViaApi(page);
    await page.goto('/workbench');
    await expect(page.getByTestId('wb-app')).toBeVisible({ timeout: 60_000 });

    await page.getByRole('button', { name: 'Files & Data', exact: true }).click();
    await expect(page.getByTestId('wb-files-panel')).toBeVisible();

    await page.getByTestId('wb-files-input').setInputFiles({
      name: DATASET,
      mimeType: 'text/csv',
      buffer: Buffer.from(CSV, 'utf-8'),
    });

    // The panel promises the file "becomes queryable in Ask AURA immediately";
    // this is where that promise either holds or does not.
    await expect(page.getByText(`Uploaded ${DATASET}`)).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText(DATASET, { exact: false }).first()).toBeVisible();

    await page.getByRole('button', { name: 'Ask AURA', exact: true }).click();
    await expect(page.getByTestId('wb-ask-panel')).toBeVisible();

    const answer = await ask(page, `What is the total revenue in ${TABLE}?`);

    // Bug #6 is the reason this asserts a NUMBER and not a 200: a connector
    // pointed at :memory: answered "success, 0 rows" and looked perfectly
    // healthy. Only the value proves the query hit the real data.
    expect(
      digitsOf(answer),
      `expected total revenue ${TOTAL_REVENUE} in the answer, got:\n${answer}`,
    ).toContain(String(TOTAL_REVENUE));
  });

  test('a grouped question returns the right winner, not just a table', async ({ page }) => {
    test.setTimeout(ANSWER_TIMEOUT + 120_000);

    await loginViaApi(page);
    await page.goto('/workbench');
    await expect(page.getByTestId('wb-app')).toBeVisible({ timeout: 60_000 });

    await page.getByRole('button', { name: 'Ask AURA', exact: true }).click();
    await expect(page.getByTestId('wb-ask-panel')).toBeVisible();

    const answer = await ask(
      page,
      `In ${TABLE}, which region has the highest revenue? Show the region and its revenue.`,
    );

    // A GROUP BY that silently drops or mis-joins rows still renders a
    // plausible table; naming the correct winner is what rules that out.
    expect(answer.toLowerCase(), `expected region "${TOP_REGION}" in:\n${answer}`).toContain(TOP_REGION);
    expect(digitsOf(answer)).toContain(String(Math.max(...ROWS.map((r) => r.revenue))));
  });
});

/**
 * Log in through the API and seed the token the app reads. The UI login path
 * has its own test above; repeating it as setup would just make every other
 * spec slower and give a form regression two unrelated places to fail.
 */
async function loginViaApi(page: Page): Promise<void> {
  const resp = await page.request.post(`${BASE}/api/v1/auth/token`, {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(resp.ok(), `login failed: ${resp.status()} ${await resp.text()}`).toBeTruthy();

  const { access_token: token } = (await resp.json()) as { access_token: string };
  await page.addInitScript(
    ([k, v]) => window.localStorage.setItem(k, v),
    [TOKEN_KEY, token],
  );
}

/** Ask a question and return the assistant's rendered text once it settles. */
async function ask(page: Page, question: string): Promise<string> {
  const panel = page.getByTestId('wb-ask-panel');

  await page.getByRole('textbox', { name: 'Ask AURA' }).fill(question);
  // Scoped to the panel on purpose: the sidebar's "Ask AURA" nav item is also
  // a role=button whose accessible name contains "Ask", so an unscoped lookup
  // is a strict-mode violation rather than a click.
  await panel.getByRole('button', { name: 'Ask' }).click();
  // The pending row is the panel's own "working" signal; waiting for it to go
  // away is what distinguishes a slow answer from a finished one.
  await expect(panel.getByText('generating SQL')).toBeHidden({ timeout: ANSWER_TIMEOUT });

  const text = (await panel.innerText()).trim();
  expect(text, 'the assistant produced no answer at all').not.toBe('');
  return text;
}

/** Strip thousands separators so 1,413,277 and 1413277 compare equal. */
function digitsOf(text: string): string {
  return text.replace(/,/g, '');
}
