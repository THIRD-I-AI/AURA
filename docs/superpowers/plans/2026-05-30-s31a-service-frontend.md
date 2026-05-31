# S31a — Service Front Door (Frontend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reposition AURA's frontend as a public audit *service* — a chrome-free front door where a visitor picks a compliance scenario, watches the audit run live, and gets a formal, independently-verifiable certificate.

**Architecture:** Introduce `react-router-dom` at the root. The entire existing dashboard mounts unchanged under `/app/*`; new public routes (`/`, `/audit/new`, `/audit/:jobId`, `/certificate/:hash`, `/verify/:hash`) render through a chrome-free `PublicShell` that mounts none of the dashboard's auth/data hooks. All new code lives in `frontend/src/audit/` and consumes only the frozen S31b backend contract.

**Tech Stack:** React 19, TypeScript, Vite, Vitest + Testing Library, `react-router-dom` v7.

**Spec:** `docs/superpowers/specs/2026-05-30-s31a-service-frontend-design.md`

**Conventions to follow (verified in repo):**
- Tests: `import { render, screen } from '@testing-library/react'`, `userEvent` from `@testing-library/user-event`, `describe/expect/it/vi` from `vitest`. Use `data-testid` for queries.
- Components using router hooks must be wrapped in `<MemoryRouter>` (or `<MemoryRouter initialEntries={[...]}>`) in tests.
- Mock fetch with `vi.stubGlobal('fetch', vi.fn())` in `beforeEach`; restore with `vi.unstubAllGlobals()` in `afterEach`.
- API base: `import { API_BASE_URL } from '../services/api'` → e.g. `${API_BASE_URL}/counterfactual/...`.
- Pre-push protocol (run before every commit that touches frontend): `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run`. All commands run from `frontend/`.
- Commit co-author line: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/audit/types.ts` | Shared TypeScript types for the frozen S31b contract (Scenario, JobSnapshot, Artifact, Estimate, VerifyResult). |
| `frontend/src/audit/auditApi.ts` | Thin typed client over the 6 S31b endpoints. Pure functions, fetch-based, reuse `API_BASE_URL`. |
| `frontend/src/audit/useJobPolling.ts` | React hook: polls `/jobs/:id` on an interval, stops on terminal state, surfaces `degraded`/`failed`. |
| `frontend/src/audit/PublicShell.tsx` | Chrome-free layout wrapper (logo + minimal footer). No auth/data hooks. |
| `frontend/src/audit/AuditFrontDoor.tsx` | `/` — hero + scenario grid. |
| `frontend/src/audit/AuditProgress.tsx` | `/audit/:jobId` — estimator checklist driven by polling. |
| `frontend/src/audit/Certificate.tsx` | Pure presentational formal certificate (takes `artifact` + optional `verifyResult`, `readOnly`). |
| `frontend/src/audit/CertificatePage.tsx` | `/certificate/:hash` — loads artifact, renders `<Certificate>`. |
| `frontend/src/audit/VerifyPage.tsx` | `/verify/:hash` — server-verified read, renders `<Certificate readOnly>`. |
| `frontend/src/audit/AuditWizard.tsx` | `/audit/new` — guided custom-audit form replacing the raw-JSON editor. |
| `frontend/src/AppRoutes.tsx` | Top-level `<Routes>` wiring public routes + `/app/*` dashboard catch-all. |
| `frontend/src/main.tsx` | Modify: wrap render tree in `<BrowserRouter>`, render `<AppRoutes/>` instead of `<App/>`. |
| `frontend/src/audit/__tests__/*.test.tsx` | One test file per unit above. |

---

## Task 1: Add router + restructure app shell

**Files:**
- Modify: `frontend/package.json` (add dependency)
- Create: `frontend/src/audit/PublicShell.tsx`
- Create: `frontend/src/AppRoutes.tsx`
- Modify: `frontend/src/main.tsx`
- Test: `frontend/src/audit/__tests__/AppRoutes.test.tsx`

- [ ] **Step 1: Install react-router-dom**

Run (from `frontend/`): `npm install react-router-dom@^7`
Expected: `package.json` gains `"react-router-dom": "^7.x"` under dependencies; `npm install` exits 0.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/audit/__tests__/AppRoutes.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../../AppRoutes';

describe('AppRoutes', () => {
  it('renders the public front door at /', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('audit-front-door')).toBeInTheDocument();
  });

  it('renders the verify page at /verify/:hash', () => {
    render(
      <MemoryRouter initialEntries={['/verify/abc123']}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('verify-page')).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/AppRoutes.test.tsx`
Expected: FAIL — `Cannot find module '../../AppRoutes'`.

- [ ] **Step 4: Create PublicShell**

Create `frontend/src/audit/PublicShell.tsx`:

```tsx
import type { ReactNode } from 'react';

/**
 * Chrome-free wrapper for the public audit-service surfaces. Deliberately
 * mounts NONE of the dashboard's auth/data hooks — an outside regulator
 * hitting /verify must reach only the verification endpoint.
 */
export function PublicShell({ children }: { children: ReactNode }) {
  return (
    <div data-testid="public-shell" style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg-base)', color: 'var(--text-primary)' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', padding: 'var(--space-4) var(--space-6)', borderBottom: '1px solid var(--border-default)' }}>
        <span style={{ fontWeight: 700, letterSpacing: '-0.02em' }}>AURA</span>
        <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Audit Service</span>
      </header>
      <main style={{ flex: 1, width: '100%', maxWidth: 1100, margin: '0 auto', padding: 'var(--space-8) var(--space-6)' }}>
        {children}
      </main>
      <footer style={{ padding: 'var(--space-4) var(--space-6)', borderTop: '1px solid var(--border-default)', fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
        Cryptographically-verifiable compliance audits · ED25519 signed
      </footer>
    </div>
  );
}
```

- [ ] **Step 5: Create AppRoutes with placeholder route bodies**

Create `frontend/src/AppRoutes.tsx`. Use lazy imports for the dashboard so it isn't pulled into the public bundle. The audit pages are imported eagerly (small). For Task 1 the not-yet-built pages render minimal stubs with the right test ids; later tasks replace the stub bodies.

```tsx
import { lazy, Suspense } from 'react';
import { Routes, Route } from 'react-router-dom';

import { PublicShell } from './audit/PublicShell';
import { AuditFrontDoor } from './audit/AuditFrontDoor';
import { AuditProgress } from './audit/AuditProgress';
import { CertificatePage } from './audit/CertificatePage';
import { VerifyPage } from './audit/VerifyPage';
import { AuditWizard } from './audit/AuditWizard';

const Dashboard = lazy(() => import('./App'));

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<PublicShell><AuditFrontDoor /></PublicShell>} />
      <Route path="/audit/new" element={<PublicShell><AuditWizard /></PublicShell>} />
      <Route path="/audit/:jobId" element={<PublicShell><AuditProgress /></PublicShell>} />
      <Route path="/certificate/:hash" element={<PublicShell><CertificatePage /></PublicShell>} />
      <Route path="/verify/:hash" element={<PublicShell><VerifyPage /></PublicShell>} />
      <Route path="/app/*" element={<Suspense fallback={<div>Loading…</div>}><Dashboard /></Suspense>} />
    </Routes>
  );
}
```

- [ ] **Step 6: Create minimal stub pages so the module resolves**

Create each of these with a minimal body carrying the test id (later tasks flesh them out). Example `frontend/src/audit/AuditFrontDoor.tsx`:

```tsx
export function AuditFrontDoor() {
  return <div data-testid="audit-front-door" />;
}
```

Create the same shape for: `AuditProgress.tsx` (`data-testid="audit-progress"`), `CertificatePage.tsx` (`data-testid="certificate-page"`), `VerifyPage.tsx` (`data-testid="verify-page"`), `AuditWizard.tsx` (`data-testid="audit-wizard"`). Each is a single named export matching the import in `AppRoutes.tsx`.

- [ ] **Step 7: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/AppRoutes.test.tsx`
Expected: PASS (both cases).

- [ ] **Step 8: Wire the router into main.tsx**

Modify `frontend/src/main.tsx`: add `import { BrowserRouter } from 'react-router-dom';` and `import { AppRoutes } from './AppRoutes';`. Replace `<ThemeProvider><App /></ThemeProvider>` with:

```tsx
<ThemeProvider>
  <BrowserRouter>
    <AppRoutes />
  </BrowserRouter>
</ThemeProvider>
```

Remove the now-unused `import App from './App.tsx'` line (App is imported lazily inside AppRoutes).

- [ ] **Step 9: Full pre-push gate**

Run: `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run`
Expected: all pass. (If any existing dashboard test renders a component that now needs a Router and breaks, wrap that test's render in `<MemoryRouter>` — but the dashboard mounts under `/app/*` unchanged, so this is unlikely.)

- [ ] **Step 10: Commit**

```bash
cd frontend && git add package.json package-lock.json src/main.tsx src/AppRoutes.tsx src/audit/
git commit -m "feat(s31a): add router shell; mount dashboard under /app/*

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Contract types + typed API client

**Files:**
- Create: `frontend/src/audit/types.ts`
- Create: `frontend/src/audit/auditApi.ts`
- Test: `frontend/src/audit/__tests__/auditApi.test.ts`

- [ ] **Step 1: Define contract types**

Create `frontend/src/audit/types.ts`:

```ts
export interface Scenario {
  id: string;
  title: string;
  vertical: string;
  description: string;
}

export interface Estimate {
  method: string;
  point_estimate?: number;
  ci_low?: number;
  ci_high?: number;
  error?: string;
}

export interface Artifact {
  audit_record_hash: string;
  estimates: Estimate[];
  refutations: unknown[];
  signature_status: string;
  signing_key_source: string;
  rendered?: unknown;
}

export type JobState = 'queued' | 'running' | 'succeeded' | 'failed';

export interface JobSnapshot {
  job_id: string;
  state: JobState;
  artifact?: Artifact;
  error?: string;
}

export interface DemoSubmitResult {
  job_id: string;
  scenario_id: string;
  degraded: boolean;
}

export interface VerifyResult {
  record_hash: string;
  verified: boolean;
  signature_status: string;
  signing_key_source: string;
  reason?: string;
}
```

- [ ] **Step 2: Write the failing test**

Create `frontend/src/audit/__tests__/auditApi.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { auditApi } from '../auditApi';
import { API_BASE_URL } from '../../services/api';

function mockJson(body: unknown, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) } as Response);
}

describe('auditApi', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('listScenarios GETs the demo scenarios endpoint and unwraps the array', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ scenarios: [{ id: 'fair_lending', title: 'T', vertical: 'compliance', description: 'd' }] }));
    const out = await auditApi.listScenarios();
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/demo/scenarios`);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe('fair_lending');
  });

  it('runScenario POSTs to the demo scenario endpoint', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ job_id: 'ca_1', scenario_id: 'fair_lending', degraded: false }));
    const out = await auditApi.runScenario('fair_lending');
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/demo/fair_lending`, expect.objectContaining({ method: 'POST' }));
    expect(out.job_id).toBe('ca_1');
  });

  it('getJob GETs the job snapshot', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ job_id: 'ca_1', state: 'running' }));
    const out = await auditApi.getJob('ca_1');
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/jobs/ca_1`);
    expect(out.state).toBe('running');
  });

  it('verify GETs the artifact verify endpoint', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ record_hash: 'h', verified: true, signature_status: 'ok', signing_key_source: 'persisted_file' }));
    const out = await auditApi.verify('h');
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/artifacts/h/verify`);
    expect(out.verified).toBe(true);
  });

  it('throws on non-ok responses', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ detail: 'boom' }, false, 500));
    await expect(auditApi.getJob('ca_1')).rejects.toThrow(/500/);
  });

  it('pdfUrl and verifyPath build correct hrefs without fetching', () => {
    expect(auditApi.pdfUrl('h')).toBe(`${API_BASE_URL}/counterfactual/artifacts/h/report.pdf`);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/auditApi.test.ts`
Expected: FAIL — `Cannot find module '../auditApi'`.

- [ ] **Step 4: Implement auditApi**

Create `frontend/src/audit/auditApi.ts`:

```ts
import { API_BASE_URL } from '../services/api';
import type { Scenario, JobSnapshot, DemoSubmitResult, VerifyResult, Artifact } from './types';

const CF = `${API_BASE_URL}/counterfactual`;

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

export const auditApi = {
  async listScenarios(): Promise<Scenario[]> {
    const body = await getJson<{ scenarios: Scenario[] }>(`${CF}/demo/scenarios`);
    return body.scenarios;
  },

  async runScenario(scenarioId: string): Promise<DemoSubmitResult> {
    const resp = await fetch(`${CF}/demo/${scenarioId}`, { method: 'POST' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json() as Promise<DemoSubmitResult>;
  },

  getJob(jobId: string): Promise<JobSnapshot> {
    return getJson<JobSnapshot>(`${CF}/jobs/${jobId}`);
  },

  getArtifact(hash: string): Promise<Artifact> {
    return getJson<Artifact>(`${CF}/artifacts/${hash}`);
  },

  verify(hash: string): Promise<VerifyResult> {
    return getJson<VerifyResult>(`${CF}/artifacts/${hash}/verify`);
  },

  pdfUrl(hash: string): string {
    return `${CF}/artifacts/${hash}/report.pdf`;
  },
};
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/auditApi.test.ts`
Expected: PASS (all 6 cases).

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/audit/types.ts src/audit/auditApi.ts src/audit/__tests__/auditApi.test.ts
git commit -m "feat(s31a): typed audit API client over frozen S31b contract

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Job polling hook

**Files:**
- Create: `frontend/src/audit/useJobPolling.ts`
- Test: `frontend/src/audit/__tests__/useJobPolling.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/audit/__tests__/useJobPolling.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

import { useJobPolling } from '../useJobPolling';
import { auditApi } from '../auditApi';

describe('useJobPolling', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it('polls until a terminal state then stops', async () => {
    const getJob = vi.spyOn(auditApi, 'getJob')
      .mockResolvedValueOnce({ job_id: 'j', state: 'running' })
      .mockResolvedValueOnce({ job_id: 'j', state: 'succeeded', artifact: { audit_record_hash: 'h', estimates: [], refutations: [], signature_status: 'ok', signing_key_source: 'persisted_file' } });

    const { result } = renderHook(() => useJobPolling('j', 800));

    await waitFor(() => expect(result.current.snapshot?.state).toBe('running'));
    await vi.advanceTimersByTimeAsync(800);
    await waitFor(() => expect(result.current.snapshot?.state).toBe('succeeded'));

    const callsAtDone = getJob.mock.calls.length;
    await vi.advanceTimersByTimeAsync(2000);
    expect(getJob.mock.calls.length).toBe(callsAtDone); // stopped polling
  });

  it('exposes failed state', async () => {
    vi.spyOn(auditApi, 'getJob').mockResolvedValue({ job_id: 'j', state: 'failed', error: 'nope' });
    const { result } = renderHook(() => useJobPolling('j', 800));
    await waitFor(() => expect(result.current.snapshot?.state).toBe('failed'));
    expect(result.current.snapshot?.error).toBe('nope');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/useJobPolling.test.ts`
Expected: FAIL — `Cannot find module '../useJobPolling'`.

- [ ] **Step 3: Implement the hook**

Create `frontend/src/audit/useJobPolling.ts`:

```ts
import { useEffect, useRef, useState } from 'react';
import { auditApi } from './auditApi';
import type { JobSnapshot } from './types';

const TERMINAL = new Set(['succeeded', 'failed']);

export interface JobPollingState {
  snapshot: JobSnapshot | null;
  error: string | null;
}

export function useJobPolling(jobId: string | undefined, intervalMs = 800): JobPollingState {
  const [snapshot, setSnapshot] = useState<JobSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stopped = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    stopped.current = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const snap = await auditApi.getJob(jobId);
        if (stopped.current) return;
        setSnapshot(snap);
        if (TERMINAL.has(snap.state)) return; // terminal — stop scheduling
      } catch (e) {
        if (stopped.current) return;
        setError(e instanceof Error ? e.message : String(e));
      }
      timer = setTimeout(tick, intervalMs);
    };

    tick();
    return () => { stopped.current = true; clearTimeout(timer); };
  }, [jobId, intervalMs]);

  return { snapshot, error };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/useJobPolling.test.ts`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/audit/useJobPolling.ts src/audit/__tests__/useJobPolling.test.ts
git commit -m "feat(s31a): useJobPolling hook (stops on terminal state)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Front door (scenario grid)

**Files:**
- Modify: `frontend/src/audit/AuditFrontDoor.tsx` (replace Task 1 stub)
- Test: `frontend/src/audit/__tests__/AuditFrontDoor.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/audit/__tests__/AuditFrontDoor.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

import { AuditFrontDoor } from '../AuditFrontDoor';
import { auditApi } from '../auditApi';

describe('AuditFrontDoor', () => {
  beforeEach(() => { navigate.mockClear(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('renders one card per scenario', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([
      { id: 'fair_lending', title: 'Fair Lending', vertical: 'compliance', description: 'd1' },
      { id: 'insurance', title: 'Insurance', vertical: 'insurance', description: 'd2' },
    ]);
    render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('scenario-card-fair_lending')).toBeInTheDocument());
    expect(screen.getByTestId('scenario-card-insurance')).toBeInTheDocument();
  });

  it('clicking a card runs the scenario and navigates to its job', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([
      { id: 'fair_lending', title: 'Fair Lending', vertical: 'compliance', description: 'd1' },
    ]);
    vi.spyOn(auditApi, 'runScenario').mockResolvedValue({ job_id: 'ca_9', scenario_id: 'fair_lending', degraded: false });
    render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('scenario-card-fair_lending'));
    await userEvent.click(screen.getByTestId('scenario-card-fair_lending'));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/audit/ca_9'));
  });

  it('shows a retry affordance when scenarios fail to load', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockRejectedValue(new Error('offline'));
    render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('scenarios-error')).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/AuditFrontDoor.test.tsx`
Expected: FAIL — front door has no scenario cards yet (stub).

- [ ] **Step 3: Implement the front door**

Replace `frontend/src/audit/AuditFrontDoor.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auditApi } from './auditApi';
import type { Scenario } from './types';

export function AuditFrontDoor() {
  const navigate = useNavigate();
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);

  const load = () => {
    setError(null);
    auditApi.listScenarios().then(setScenarios).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(load, []);

  const run = async (id: string) => {
    setLaunching(id);
    try {
      const { job_id } = await auditApi.runScenario(id);
      navigate(`/audit/${job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLaunching(null);
    }
  };

  return (
    <div data-testid="audit-front-door">
      <h1 style={{ fontSize: 'var(--font-3xl)', fontWeight: 700, letterSpacing: '-0.03em' }}>
        Cryptographically-verifiable compliance audits
      </h1>
      <p style={{ color: 'var(--text-tertiary)', marginBottom: 'var(--space-8)' }}>
        Pick a regulated-decision scenario. Watch the audit run. Get a signed certificate anyone can verify.
      </p>

      {error && (
        <div data-testid="scenarios-error" style={{ padding: 'var(--space-5)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-lg)' }}>
          Couldn’t load scenarios. <button onClick={load}>Retry</button>
        </div>
      )}

      {!scenarios && !error && <p>Loading scenarios…</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 'var(--space-5)' }}>
        {scenarios?.map((s) => (
          <button
            key={s.id}
            data-testid={`scenario-card-${s.id}`}
            onClick={() => run(s.id)}
            disabled={launching !== null}
            style={{ textAlign: 'left', background: 'var(--bg-surface)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)', cursor: 'pointer' }}
          >
            <span style={{ fontSize: 'var(--font-xs)', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-tertiary)' }}>{s.vertical}</span>
            <h3 style={{ margin: 'var(--space-2) 0' }}>{s.title}</h3>
            <p style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>{s.description}</p>
            <span style={{ fontSize: 'var(--font-sm)', color: 'var(--accent)' }}>{launching === s.id ? 'Starting…' : 'Run audit →'}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/AuditFrontDoor.test.tsx`
Expected: PASS (all 3 cases).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/audit/AuditFrontDoor.tsx src/audit/__tests__/AuditFrontDoor.test.tsx
git commit -m "feat(s31a): front door scenario grid

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Live progress (estimator checklist)

**Files:**
- Modify: `frontend/src/audit/AuditProgress.tsx` (replace Task 1 stub)
- Test: `frontend/src/audit/__tests__/AuditProgress.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/audit/__tests__/AuditProgress.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

import { AuditProgress } from '../AuditProgress';
import * as polling from '../useJobPolling';

function renderAt(jobId: string) {
  return render(
    <MemoryRouter initialEntries={[`/audit/${jobId}`]}>
      <Routes><Route path="/audit/:jobId" element={<AuditProgress />} /></Routes>
    </MemoryRouter>,
  );
}

describe('AuditProgress', () => {
  afterEach(() => { vi.restoreAllMocks(); navigate.mockClear(); });

  it('renders an estimator row per estimate in the running snapshot', () => {
    vi.spyOn(polling, 'useJobPolling').mockReturnValue({
      snapshot: { job_id: 'j', state: 'running', artifact: { audit_record_hash: '', refutations: [], signature_status: '', signing_key_source: '', estimates: [
        { method: 'linear_regression', point_estimate: 0.12, ci_low: 0.05, ci_high: 0.2 },
        { method: 'double_ml' },
      ] } },
      error: null,
    });
    renderAt('j');
    expect(screen.getByTestId('estimator-linear_regression')).toBeInTheDocument();
    expect(screen.getByTestId('estimator-double_ml')).toBeInTheDocument();
  });

  it('navigates to the certificate when the job succeeds', async () => {
    vi.spyOn(polling, 'useJobPolling').mockReturnValue({
      snapshot: { job_id: 'j', state: 'succeeded', artifact: { audit_record_hash: 'HASH', refutations: [], signature_status: 'ok', signing_key_source: 'persisted_file', estimates: [] } },
      error: null,
    });
    renderAt('j');
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/certificate/HASH'));
  });

  it('shows a failure panel on failed state', () => {
    vi.spyOn(polling, 'useJobPolling').mockReturnValue({ snapshot: { job_id: 'j', state: 'failed', error: 'engine error' }, error: null });
    renderAt('j');
    expect(screen.getByTestId('audit-failed')).toHaveTextContent('engine error');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/AuditProgress.test.tsx`
Expected: FAIL — stub has no estimator rows.

- [ ] **Step 3: Implement progress**

Replace `frontend/src/audit/AuditProgress.tsx`:

```tsx
import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useJobPolling } from './useJobPolling';
import type { Estimate } from './types';

function EstimatorRow({ e }: { e: Estimate }) {
  const done = e.point_estimate !== undefined || e.error !== undefined;
  return (
    <div data-testid={`estimator-${e.method}`} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-3) 0', borderBottom: '1px solid var(--border-default)' }}>
      <span style={{ width: 14, height: 14, borderRadius: '50%', background: e.error ? 'var(--red)' : done ? 'var(--green)' : 'var(--accent)', flexShrink: 0 }} />
      <span style={{ flex: 1, fontWeight: 500 }}>{e.method}</span>
      <span style={{ fontFamily: 'monospace', fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>
        {e.error ? 'n/a' : e.point_estimate !== undefined ? `${e.point_estimate.toFixed(3)} [${e.ci_low?.toFixed(2)}, ${e.ci_high?.toFixed(2)}]` : 'running…'}
      </span>
    </div>
  );
}

export function AuditProgress() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const { snapshot } = useJobPolling(jobId, 800);

  useEffect(() => {
    if (snapshot?.state === 'succeeded' && snapshot.artifact) {
      navigate(`/certificate/${snapshot.artifact.audit_record_hash}`);
    }
  }, [snapshot, navigate]);

  if (snapshot?.state === 'failed') {
    return <div data-testid="audit-failed" style={{ padding: 'var(--space-6)' }}>
      <h2>Audit could not complete</h2>
      <p style={{ color: 'var(--text-tertiary)' }}>{snapshot.error}</p>
      <button onClick={() => navigate('/')}>Back to scenarios</button>
    </div>;
  }

  const estimates = snapshot?.artifact?.estimates ?? [];

  return (
    <div data-testid="audit-progress">
      <h2>Running audit…</h2>
      <p style={{ color: 'var(--text-tertiary)' }}>State: {snapshot?.state ?? 'queued'}</p>
      <div>{estimates.map((e) => <EstimatorRow key={e.method} e={e} />)}</div>
      {estimates.length === 0 && <p>Spinning up estimators…</p>}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/AuditProgress.test.tsx`
Expected: PASS (all 3 cases).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/audit/AuditProgress.tsx src/audit/__tests__/AuditProgress.test.tsx
git commit -m "feat(s31a): live estimator-checklist progress view

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Certificate component + page

**Files:**
- Create: `frontend/src/audit/Certificate.tsx`
- Modify: `frontend/src/audit/CertificatePage.tsx` (replace Task 1 stub)
- Test: `frontend/src/audit/__tests__/Certificate.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/audit/__tests__/Certificate.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { Certificate } from '../Certificate';
import type { Artifact } from '../types';

const artifact: Artifact = {
  audit_record_hash: 'a3f9c1deadbeef',
  estimates: [{ method: 'tmle', point_estimate: 0.08, ci_low: 0.01, ci_high: 0.15 }],
  refutations: [],
  signature_status: 'signed',
  signing_key_source: 'persisted_file',
};

describe('Certificate', () => {
  it('shows the hash, signature badge and key source', () => {
    render(<MemoryRouter><Certificate artifact={artifact} /></MemoryRouter>);
    expect(screen.getByTestId('cert-hash')).toHaveTextContent('a3f9c1deadbeef');
    expect(screen.getByTestId('cert-signature-badge')).toHaveTextContent(/signed/i);
    expect(screen.getByTestId('cert-key-source')).toHaveTextContent('persisted_file');
  });

  it('renders PDF + verify actions by default', () => {
    render(<MemoryRouter><Certificate artifact={artifact} /></MemoryRouter>);
    expect(screen.getByTestId('cert-download-pdf')).toBeInTheDocument();
    expect(screen.getByTestId('cert-verify-link')).toBeInTheDocument();
  });

  it('hides actions in readOnly mode (used by the public verify page)', () => {
    render(<MemoryRouter><Certificate artifact={artifact} readOnly /></MemoryRouter>);
    expect(screen.queryByTestId('cert-download-pdf')).not.toBeInTheDocument();
  });

  it('shows NOT-verified state when verifyResult says so', () => {
    render(<MemoryRouter><Certificate artifact={artifact} readOnly verifyResult={{ record_hash: 'a3f9c1deadbeef', verified: false, signature_status: 'bad', signing_key_source: 'persisted_file', reason: 'signature mismatch' }} /></MemoryRouter>);
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent(/not verified/i);
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent('signature mismatch');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/Certificate.test.tsx`
Expected: FAIL — `Cannot find module '../Certificate'`.

- [ ] **Step 3: Implement Certificate**

Create `frontend/src/audit/Certificate.tsx`:

```tsx
import { Link } from 'react-router-dom';
import { auditApi } from './auditApi';
import type { Artifact, VerifyResult } from './types';

/** Plain-English verdict from the estimates (used when no scenario narrative). */
function verdict(a: Artifact): string {
  const pts = a.estimates.map((e) => e.point_estimate).filter((v): v is number => v !== undefined);
  if (pts.length === 0) return 'Audit complete — see estimator detail.';
  const avg = pts.reduce((s, v) => s + v, 0) / pts.length;
  const material = Math.abs(avg) >= 0.02;
  return material
    ? 'Disparate impact detected after causal adjustment.'
    : 'No material disparate impact detected after causal adjustment.';
}

export function Certificate({ artifact, verifyResult, readOnly = false }: {
  artifact: Artifact;
  verifyResult?: VerifyResult;
  readOnly?: boolean;
}) {
  const ok = verifyResult ? verifyResult.verified : artifact.signature_status === 'signed';

  return (
    <div data-testid="certificate" style={{ maxWidth: 720, margin: '0 auto', background: 'var(--bg-surface)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-8)', textAlign: 'center' }}>
      <div data-testid="cert-signature-badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)', padding: 'var(--space-1) var(--space-3)', borderRadius: 'var(--radius-full)', background: ok ? 'var(--green-bg, rgba(76,175,80,0.15))' : 'var(--red-bg, rgba(244,67,54,0.15))', color: ok ? 'var(--green)' : 'var(--red)', fontWeight: 600, fontSize: 'var(--font-sm)' }}>
        {ok ? '✓ ED25519 signed' : '✕ signature ' + artifact.signature_status}
      </div>

      <h1 style={{ fontSize: 'var(--font-2xl)', margin: 'var(--space-5) 0 var(--space-2)' }}>Audit Certificate</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--space-6)' }}>{verdict(artifact)}</p>

      <div style={{ textAlign: 'left', background: 'var(--bg-base)', borderRadius: 'var(--radius-md)', padding: 'var(--space-4)', marginBottom: 'var(--space-6)' }}>
        <div style={{ fontSize: 'var(--font-xs)', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-tertiary)' }}>Record hash</div>
        <div data-testid="cert-hash" style={{ fontFamily: 'monospace', wordBreak: 'break-all', color: 'var(--accent)' }}>{artifact.audit_record_hash}</div>
        <div data-testid="cert-key-source" style={{ marginTop: 'var(--space-3)', fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>Key source: {artifact.signing_key_source}</div>
      </div>

      {verifyResult && (
        <div data-testid="cert-verify-status" style={{ marginBottom: 'var(--space-5)', fontWeight: 600, color: verifyResult.verified ? 'var(--green)' : 'var(--red)' }}>
          {verifyResult.verified ? 'Independently verified ✓' : `NOT verified — ${verifyResult.reason ?? verifyResult.signature_status}`}
        </div>
      )}

      {!readOnly && (
        <div style={{ display: 'flex', gap: 'var(--space-3)', justifyContent: 'center' }}>
          <a data-testid="cert-download-pdf" href={auditApi.pdfUrl(artifact.audit_record_hash)} target="_blank" rel="noreferrer" style={{ padding: 'var(--space-2) var(--space-5)', background: 'var(--accent)', color: '#fff', borderRadius: 'var(--radius-md)', textDecoration: 'none' }}>Download PDF</a>
          <Link data-testid="cert-verify-link" to={`/verify/${artifact.audit_record_hash}`} style={{ padding: 'var(--space-2) var(--space-5)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-md)', textDecoration: 'none', color: 'var(--text-primary)' }}>Verify independently</Link>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Implement CertificatePage (loads artifact by hash)**

Replace `frontend/src/audit/CertificatePage.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { auditApi } from './auditApi';
import { Certificate } from './Certificate';
import type { Artifact } from './types';

export function CertificatePage() {
  const { hash } = useParams<{ hash: string }>();
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hash) return;
    auditApi.getArtifact(hash).then(setArtifact).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [hash]);

  if (error) return <div data-testid="certificate-page">Could not load certificate: {error}</div>;
  if (!artifact) return <div data-testid="certificate-page">Loading certificate…</div>;
  return <div data-testid="certificate-page"><Certificate artifact={artifact} /></div>;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/Certificate.test.tsx`
Expected: PASS (all 4 cases).

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/audit/Certificate.tsx src/audit/CertificatePage.tsx src/audit/__tests__/Certificate.test.tsx
git commit -m "feat(s31a): formal certificate component + page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Public verify page

**Files:**
- Modify: `frontend/src/audit/VerifyPage.tsx` (replace Task 1 stub)
- Test: `frontend/src/audit/__tests__/VerifyPage.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/audit/__tests__/VerifyPage.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import { VerifyPage } from '../VerifyPage';
import { auditApi } from '../auditApi';

function renderAt(hash: string) {
  return render(
    <MemoryRouter initialEntries={[`/verify/${hash}`]}>
      <Routes><Route path="/verify/:hash" element={<VerifyPage />} /></Routes>
    </MemoryRouter>,
  );
}

describe('VerifyPage', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('shows the verified certificate for a good hash', async () => {
    vi.spyOn(auditApi, 'verify').mockResolvedValue({ record_hash: 'good', verified: true, signature_status: 'signed', signing_key_source: 'persisted_file' });
    vi.spyOn(auditApi, 'getArtifact').mockResolvedValue({ audit_record_hash: 'good', estimates: [], refutations: [], signature_status: 'signed', signing_key_source: 'persisted_file' });
    renderAt('good');
    await waitFor(() => expect(screen.getByTestId('cert-verify-status')).toHaveTextContent(/verified/i));
  });

  it('shows an explicit NOT-verified state for an invalid hash', async () => {
    vi.spyOn(auditApi, 'verify').mockResolvedValue({ record_hash: 'bad', verified: false, signature_status: 'unknown', signing_key_source: 'none', reason: 'unknown artifact' });
    vi.spyOn(auditApi, 'getArtifact').mockResolvedValue({ audit_record_hash: 'bad', estimates: [], refutations: [], signature_status: 'unknown', signing_key_source: 'none' });
    renderAt('bad');
    await waitFor(() => expect(screen.getByTestId('cert-verify-status')).toHaveTextContent(/not verified/i));
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent('unknown artifact');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/VerifyPage.test.tsx`
Expected: FAIL — stub renders no verify status.

- [ ] **Step 3: Implement VerifyPage**

Replace `frontend/src/audit/VerifyPage.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { auditApi } from './auditApi';
import { Certificate } from './Certificate';
import type { Artifact, VerifyResult } from './types';

export function VerifyPage() {
  const { hash } = useParams<{ hash: string }>();
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hash) return;
    // Verdict comes from the SERVER-recomputed verify endpoint, never the
    // artifact's self-reported status (compliance requirement).
    auditApi.verify(hash).then(setVerifyResult).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    auditApi.getArtifact(hash).then(setArtifact).catch(() => { /* artifact body is presentational only */ });
  }, [hash]);

  if (error) return <div data-testid="verify-page">Verification unavailable: {error}</div>;
  if (!verifyResult) return <div data-testid="verify-page">Verifying…</div>;

  const shown: Artifact = artifact ?? {
    audit_record_hash: verifyResult.record_hash,
    estimates: [], refutations: [],
    signature_status: verifyResult.signature_status,
    signing_key_source: verifyResult.signing_key_source,
  };

  return (
    <div data-testid="verify-page">
      <Certificate artifact={shown} verifyResult={verifyResult} readOnly />
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/VerifyPage.test.tsx`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/audit/VerifyPage.tsx src/audit/__tests__/VerifyPage.test.tsx
git commit -m "feat(s31a): public verify page (server-recomputed trust)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Custom-audit wizard

**Files:**
- Modify: `frontend/src/audit/AuditWizard.tsx` (replace Task 1 stub)
- Test: `frontend/src/audit/__tests__/AuditWizard.test.tsx`

**Note:** The wizard submits a custom `CounterfactualQuery` to the existing job endpoint `POST /counterfactual/jobs` (the same one `Counterfactual.tsx:62` uses), then navigates to `/audit/{job_id}`. Add a `submitCustomAudit` method to `auditApi` so the wizard does not re-implement fetch.

- [ ] **Step 1: Extend auditApi with submitCustomAudit (+ test)**

Add to `frontend/src/audit/auditApi.ts` inside the `auditApi` object:

```ts
  async submitCustomAudit(query: Record<string, unknown>): Promise<{ job_id: string }> {
    const resp = await fetch(`${CF}/jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(query),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json() as Promise<{ job_id: string }>;
  },
```

Add to `frontend/src/audit/__tests__/auditApi.test.ts`:

```ts
  it('submitCustomAudit POSTs the query to the jobs endpoint', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ job_id: 'ca_5' }));
    const out = await auditApi.submitCustomAudit({ treatment: 't', outcome: 'y' });
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/jobs`, expect.objectContaining({ method: 'POST' }));
    expect(out.job_id).toBe('ca_5');
  });
```

Run: `npx vitest run src/audit/__tests__/auditApi.test.ts` → Expected: PASS.

- [ ] **Step 2: Write the failing wizard test**

Create `frontend/src/audit/__tests__/AuditWizard.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

import { AuditWizard } from '../AuditWizard';
import { auditApi } from '../auditApi';

describe('AuditWizard', () => {
  afterEach(() => { vi.restoreAllMocks(); navigate.mockClear(); });

  it('submits the assembled query and navigates to the job', async () => {
    const spy = vi.spyOn(auditApi, 'submitCustomAudit').mockResolvedValue({ job_id: 'ca_7' });
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);

    await userEvent.type(screen.getByTestId('wizard-treatment'), 'protected_class');
    await userEvent.type(screen.getByTestId('wizard-outcome'), 'approved');
    await userEvent.type(screen.getByTestId('wizard-confounders'), 'income, dti');
    await userEvent.click(screen.getByTestId('wizard-submit'));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.objectContaining({
      treatment: 'protected_class', outcome: 'approved', confounders: ['income', 'dti'],
    })));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/audit/ca_7'));
  });

  it('blocks submit until treatment and outcome are filled', async () => {
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);
    expect(screen.getByTestId('wizard-submit')).toBeDisabled();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/AuditWizard.test.tsx`
Expected: FAIL — stub has no form fields.

- [ ] **Step 4: Implement the wizard**

Replace `frontend/src/audit/AuditWizard.tsx`:

```tsx
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auditApi } from './auditApi';

export function AuditWizard() {
  const navigate = useNavigate();
  const [treatment, setTreatment] = useState('');
  const [outcome, setOutcome] = useState('');
  const [confounders, setConfounders] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = treatment.trim() !== '' && outcome.trim() !== '' && !busy;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const query = {
        treatment: treatment.trim(),
        outcome: outcome.trim(),
        confounders: confounders.split(',').map((c) => c.trim()).filter(Boolean),
      };
      const { job_id } = await auditApi.submitCustomAudit(query);
      navigate(`/audit/${job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const field = (testid: string, label: string, value: string, setter: (v: string) => void, placeholder: string) => (
    <label style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
      <span style={{ display: 'block', fontSize: 'var(--font-sm)', color: 'var(--text-secondary)', marginBottom: 'var(--space-1)' }}>{label}</span>
      <input data-testid={testid} value={value} placeholder={placeholder} onChange={(e) => setter(e.target.value)}
        style={{ width: '100%', padding: 'var(--space-3)', background: 'var(--bg-base)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)' }} />
    </label>
  );

  return (
    <div data-testid="audit-wizard" style={{ maxWidth: 560, margin: '0 auto' }}>
      <h2>Run a custom audit</h2>
      <p style={{ color: 'var(--text-tertiary)', marginBottom: 'var(--space-6)' }}>Define the causal question. We run the full estimator battery and seal a certificate.</p>
      {field('wizard-treatment', 'Treatment column', treatment, setTreatment, 'e.g. protected_class')}
      {field('wizard-outcome', 'Outcome column', outcome, setOutcome, 'e.g. approved')}
      {field('wizard-confounders', 'Confounders (comma-separated)', confounders, setConfounders, 'e.g. income, dti, credit_score')}
      {error && <p style={{ color: 'var(--red)' }}>{error}</p>}
      <button data-testid="wizard-submit" disabled={!canSubmit} onClick={submit}
        style={{ padding: 'var(--space-3) var(--space-6)', background: canSubmit ? 'var(--accent)' : 'var(--border-default)', color: '#fff', border: 'none', borderRadius: 'var(--radius-md)', cursor: canSubmit ? 'pointer' : 'not-allowed' }}>
        {busy ? 'Submitting…' : 'Run audit'}
      </button>
    </div>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/AuditWizard.test.tsx`
Expected: PASS (both cases).

- [ ] **Step 6: Full pre-push gate + commit**

Run: `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run`
Expected: all green.

```bash
cd frontend && git add src/audit/AuditWizard.tsx src/audit/auditApi.ts src/audit/__tests__/AuditWizard.test.tsx src/audit/__tests__/auditApi.test.ts
git commit -m "feat(s31a): custom-audit wizard (replaces raw-JSON editor)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Add a link from the dashboard back to the service (optional polish)

**Files:**
- Modify: `frontend/src/audit/AuditFrontDoor.tsx` (add a discreet "Open dashboard" link to `/app`)

- [ ] **Step 1: Add a footer link in the front door**

In `AuditFrontDoor.tsx`, after the scenario grid `</div>`, add:

```tsx
      <p style={{ marginTop: 'var(--space-8)', fontSize: 'var(--font-sm)' }}>
        <a href="/audit/new" style={{ color: 'var(--accent)' }}>Run a custom audit</a>
        {' · '}
        <a href="/app" style={{ color: 'var(--text-tertiary)' }}>Open dashboard</a>
      </p>
```

- [ ] **Step 2: Pre-push gate + commit**

Run: `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run`
Expected: all green.

```bash
cd frontend && git add src/audit/AuditFrontDoor.tsx
git commit -m "feat(s31a): link front door to custom-audit + dashboard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run the full frontend gate one last time from `frontend/`: `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run` — all green.
- [ ] Manually sanity-check routing by running `npx vite` and visiting `/`, `/audit/new`, `/verify/anything`, `/app` (use `superpowers:verification-before-completion` before claiming done).
- [ ] Push the branch; confirm all 14 CI jobs (esp. Frontend Lint/Tests/Type Check) are green before opening the PR.
- [ ] Open PR titled `Sprint S31a: YC Demo Service Frontend`; do not merge until S31b's `/demo` endpoints exist on a deployable backend (the UI degrades gracefully without them, but the live demo needs both).

## Spec coverage check

- Public front door (hero + scenario grid) → Task 4 ✓
- Live estimator-checklist progress → Task 5 ✓
- Formal Audit Certificate (hash + ED25519 badge + PDF) → Task 6 ✓
- Public `/verify/{hash}` (server-recomputed) → Task 7 ✓
- Custom-audit wizard (replaces raw-JSON editor) → Task 8 ✓
- react-router-dom + dashboard under `/app/*` + chrome-free PublicShell → Task 1 ✓
- Typed client over frozen S31b contract → Task 2 ✓
- Polling (no websockets) → Task 3 ✓
- Compliance: public surfaces mount no auth/data hooks; verify trusts server → Tasks 1, 7 ✓
- All tests Tier A (fetch mocked) on existing Frontend CI lane → every task ✓
