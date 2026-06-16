# S37d — Engagements Launchpad + Auth Polish (descoped) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Finish the S37 redesign program with the two pieces the rebase onto Mounith's auth left genuinely outstanding: an **Engagements launchpad** on the internal home (the auditor-workbench overview the IA promises) and a light **AuthForm card polish** for an enterprise-grade login — both honest (real routes/data only) and low-risk.

**Architecture:** No backend changes; there is no audit-runs/certificates *list* endpoint, so the Engagements home does NOT fabricate run rows — it presents real audit-lifecycle entry points plus the existing live platform-health section. The launchpad is extracted into a standalone prop-driven component (`EngagementsLaunchpad`) so it's unit-testable without App.tsx's heavy provider tree, then mounted at the top of the dashboard default case. AuthForm is wrapped in the s37a `Card` primitive, preserving every testid and button text `auth.test` asserts.

**Tech Stack:** React 18 + TS, Vitest. Branch: `feature/s37-terminal-authority` (continues; no PR this phase).

**Parent spec:** `docs/superpowers/specs/2026-06-11-s37-frontend-redesign-design.md` §4 (Engagements home) — adapted: launchpad, not a fabricated run list. Auth identity/login/logout already shipped by Mounith (#88–91) and adopted in the rebase.

**Invariants:** preserve `auth-form`/`auth-email`/`auth-password`/`auth-name`/`auth-switch` testids and the submit button text (`/sign in/i`, `/create account/i`); full suite + `tsc` + `eslint --max-warnings 0` green before each commit; co-author `Claude Opus 4.8`.

---

### Task 1: `EngagementsLaunchpad` component (TDD)

**Files:**
- Create: `frontend/src/app/EngagementsLaunchpad.tsx`, `frontend/src/app/engagements.css`
- Test: `frontend/src/app/__tests__/EngagementsLaunchpad.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { EngagementsLaunchpad } from '../EngagementsLaunchpad';

describe('EngagementsLaunchpad', () => {
  it('renders the audit-lifecycle entry points', () => {
    render(<MemoryRouter><EngagementsLaunchpad onNavigate={() => {}} /></MemoryRouter>);
    expect(screen.getByTestId('engagements-launchpad')).toBeInTheDocument();
    expect(screen.getByTestId('launch-counterfactual')).toBeInTheDocument();
    expect(screen.getByTestId('launch-exceptions')).toBeInTheDocument();
    // "Audit your own data" is a public wizard route → a real link, not in-app nav.
    expect(screen.getByTestId('launch-own-data')).toHaveAttribute('href', '/audit/new');
  });

  it('routes in-app actions through onNavigate (not a fabricated run list)', () => {
    const onNavigate = vi.fn();
    render(<MemoryRouter><EngagementsLaunchpad onNavigate={onNavigate} /></MemoryRouter>);
    fireEvent.click(screen.getByTestId('launch-counterfactual'));
    expect(onNavigate).toHaveBeenCalledWith('counterfactual');
    fireEvent.click(screen.getByTestId('launch-exceptions'));
    expect(onNavigate).toHaveBeenCalledWith('audit-hitl');
  });
});
```

- [ ] **Step 2: Run — FAIL.** `npx vitest run src/app/__tests__/EngagementsLaunchpad.test.tsx`

- [ ] **Step 3: Implement `src/app/EngagementsLaunchpad.tsx`**

```tsx
import { Link } from 'react-router-dom';
import type { PageType } from '../components/Layout/AppLayout';
import './engagements.css';

/**
 * The auditor-workbench home launchpad. There is no "list my audit runs"
 * backend endpoint, so this presents real entry points into the audit
 * lifecycle rather than fabricating a run table — honest by construction.
 * In-app destinations go through onNavigate (URL-routed); the public audit
 * wizard is a normal link.
 */
export function EngagementsLaunchpad({ onNavigate }: { onNavigate: (page: PageType) => void }) {
  return (
    <section data-testid="engagements-launchpad" className="eng-launchpad">
      <div className="eng-launchpad__intro">
        <h2 className="eng-launchpad__title">Start an engagement</h2>
        <p className="eng-launchpad__sub">
          Run a causal audit, review flagged findings, or audit your own dataset —
          every result is ED25519-signed and independently verifiable.
        </p>
      </div>
      <div className="eng-launchpad__grid">
        <button data-testid="launch-counterfactual" className="eng-tile" onClick={() => onNavigate('counterfactual')}>
          <span className="eng-tile__kicker">Causal audit</span>
          <span className="eng-tile__title">Run a counterfactual audit</span>
          <span className="eng-tile__desc">Four estimators, refutation tests, adversarial review, hash-sealed artifact.</span>
          <span className="eng-tile__cta">Open →</span>
        </button>
        <button data-testid="launch-exceptions" className="eng-tile" onClick={() => onNavigate('audit-hitl')}>
          <span className="eng-tile__kicker">Findings</span>
          <span className="eng-tile__title">Review the exception queue</span>
          <span className="eng-tile__desc">Human decisions on flagged findings — signed, WORM-chained (AS 1215).</span>
          <span className="eng-tile__cta">Open →</span>
        </button>
        <Link data-testid="launch-own-data" to="/audit/new" className="eng-tile">
          <span className="eng-tile__kicker">Your data</span>
          <span className="eng-tile__title">Audit your own dataset</span>
          <span className="eng-tile__desc">Upload a CSV, map columns, get a signed certificate anyone can verify.</span>
          <span className="eng-tile__cta">Open →</span>
        </Link>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Create `src/app/engagements.css`**

```css
/* S37d — Engagements launchpad (auditor-workbench home). Tokens only. */
.eng-launchpad { margin-bottom: var(--space-6); }
.eng-launchpad__intro { margin-bottom: var(--space-4); }
.eng-launchpad__title { margin: 0; font-size: var(--font-xl); font-weight: 700; letter-spacing: -0.02em; }
.eng-launchpad__sub { margin: var(--space-1) 0 0; color: var(--text-secondary); font-size: var(--font-sm); max-width: 70ch; }
.eng-launchpad__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); }
.eng-tile {
  display: flex; flex-direction: column; gap: var(--space-1); text-align: left; text-decoration: none;
  background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: var(--radius-lg);
  padding: var(--space-5); cursor: pointer; transition: border-color var(--dur-fast), transform var(--dur-fast);
  font-family: var(--font-sans);
}
.eng-tile:hover { border-color: var(--accent-border); transform: translateY(-2px); }
.eng-tile__kicker { font-family: var(--font-mono); font-size: var(--font-2xs); text-transform: uppercase; letter-spacing: 0.1em; color: var(--signal); }
.eng-tile__title { font-size: var(--font-md); font-weight: 600; color: var(--text-primary); }
.eng-tile__desc { font-size: var(--font-sm); color: var(--text-secondary); }
.eng-tile__cta { margin-top: var(--space-2); font-size: var(--font-sm); font-weight: 600; color: var(--accent); }
```

- [ ] **Step 5: Run test — PASS. Commit.**

```bash
npx vitest run src/app/__tests__/EngagementsLaunchpad.test.tsx && npx eslint src --max-warnings 0
git add frontend/src/app/EngagementsLaunchpad.tsx frontend/src/app/engagements.css frontend/src/app/__tests__/EngagementsLaunchpad.test.tsx
git commit -m "feat(s37d): EngagementsLaunchpad — honest audit-lifecycle home (no fabricated run list)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Mount the launchpad on the dashboard home

**Files:**
- Modify: `frontend/src/App.tsx` (import + render at the top of the `dashboard`/default case, above the KPI strip)

- [ ] **Step 1: Import** near the other lazy/component imports in `App.tsx`:

```tsx
import { EngagementsLaunchpad } from './app/EngagementsLaunchpad';
```

- [ ] **Step 2: Render at the top of the default case.** In `renderPage()`'s `dashboard`/`default` branch, immediately inside the returned `<>`, before the `{/* ── KPI strip ─ */}` block:

```tsx
            <EngagementsLaunchpad onNavigate={setCurrentPage} />
```

- [ ] **Step 3: Verify** the full suite + types + lint (no App test exists; this is a render-path change):

```bash
npx tsc --noEmit && npx vitest run 2>&1 | tail -2 && npx eslint src --max-warnings 0
```
Expected: 223 passed (220 + 3 launchpad... note 2 launchpad tests = 222; adjust to actual), clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(s37d): lead the Engagements home with the audit launchpad

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: AuthForm enterprise card polish (preserve testids)

**Files:**
- Modify: `frontend/src/auth/AuthForm.tsx`
- Test: `frontend/src/auth/__tests__/auth.test.tsx` must stay green (do not change it)

- [ ] **Step 1: Wrap the form in the s37a `Card` primitive.** Keep the `data-testid="auth-form"` wrapper, every field testid, the error testid, the switch link, and the submit button text. Change only presentation: import the Card and place the heading + form inside it.

Add import:
```tsx
import { Card } from '../ui/Card';
```
Replace the outer `<div data-testid="auth-form" style={{ maxWidth: 420, margin: '0 auto' }}>…</div>` with a centered wrapper holding a `Card`:
```tsx
  return (
    <div data-testid="auth-form" className="auth-form-wrap">
      <Card>
        <h1 className="auth-form__title">{isSignup ? 'Create your account' : 'Welcome back'}</h1>
        <p className="auth-form__sub">{isSignup ? 'Check your data in under a minute.' : 'Sign in to your workspace.'}</p>
        {/* …existing <form>…</form> unchanged (fields, error, submit Button)… */}
        {/* …existing switch <p>…</p> unchanged… */}
      </Card>
    </div>
  );
```
Keep the `<form>`, all inputs (with their inline `inputStyle`/`labelStyle` — leave those as-is to avoid scope creep), the error `<p>`, the submit `<Button>` (unchanged text), and the switch `<p>`.

- [ ] **Step 2: Add minimal classes** to `frontend/src/auth/auth.css` (new) and import it at the top of `AuthForm.tsx` (`import './auth.css';`):

```css
.auth-form-wrap { max-width: 440px; margin: var(--space-8) auto; }
.auth-form__title { font-size: var(--font-2xl); text-align: center; margin: 0 0 var(--space-2); }
.auth-form__sub { text-align: center; color: var(--text-secondary); margin: 0 0 var(--space-6); }
```

- [ ] **Step 3: Verify auth.test + suite stay green**

```bash
npx vitest run src/auth/__tests__/auth.test.tsx && npx tsc --noEmit && npx eslint src --max-warnings 0
```
Expected: auth tests pass unchanged (testids + button text preserved), clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/auth/AuthForm.tsx frontend/src/auth/auth.css
git commit -m "feat(s37d): AuthForm on the Card primitive — enterprise login (testids preserved)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full verify + visual checkpoint + push

- [ ] **Step 1: Full gate**

```bash
cd ~/Downloads/AURA/frontend
npx vitest run 2>&1 | tail -2 && npx tsc --noEmit && npx eslint src --max-warnings 0
```
Expected: ≥224 passed, clean.

- [ ] **Step 2: Visual checkpoint** (dev server :5173):
  - `/login` — AuthForm renders as a centered enterprise card; fields + submit present.
  - Log in (open-mode dev) → `/app` — Engagements home leads with the launchpad (3 audit-lifecycle tiles) above live platform health.
  - Click "Run a counterfactual audit" → routes to `/app/counterfactual`; "Review the exception queue" → `/app/audit-hitl`; "Audit your own dataset" → `/audit/new`.
  - Console clean under CSP.

- [ ] **Step 3: Push**

```bash
cd ~/Downloads/AURA
git push origin feature/s37-terminal-authority
```

- [ ] **Step 4: Update SPRINTS in-flight note** if helpful, and report S37 as build-complete (pending the final "Land Sprint S37" PR decision).

---

## Self-Review

- **Spec §4 Engagements coverage:** delivered as an honest launchpad (real routes), explicitly NOT a fabricated run list (no list endpoint exists) — deviation documented in Architecture. Findings already shipped (Mounith's ExceptionQueue, routed in s37c). Identity/login already shipped + adopted.
- **Risk control:** launchpad is a standalone prop-driven component (unit-tested in isolation; App.tsx render-path change is one import + one line). AuthForm change preserves every `auth.test` testid + button text; form internals untouched.
- **Placeholder scan:** none — full code in every step (the AuthForm step references the existing form body explicitly rather than restating it, to avoid drift).
- **Type consistency:** `EngagementsLaunchpad` `onNavigate: (page: PageType) => void` matches `setCurrentPage`'s signature from App.tsx; page ids `'counterfactual'`/`'audit-hitl'` exist in `PageType` and `PAGE_IDS`.
