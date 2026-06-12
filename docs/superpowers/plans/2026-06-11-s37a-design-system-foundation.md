# S37a — Design System Foundation (Terminal Authority) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Terminal Authority design system — tokens with a legacy-variable compatibility layer, the `certificate` light theme, self-hosted fonts, six core primitives, and the frontend security guards — so every existing surface inherits the new look and Plan 2 (customer surface) has its building blocks.

**Architecture:** A new `tokens.css` loads AFTER `design-system.css` and overrides the ~90 color-bearing legacy custom properties (structural tokens — spacing, radii, durations — stay untouched), so all 14 panels re-skin without component edits. Themes ride the existing `data-theme` attribute (`ThemeContext`); `certificate` is a third value applied to a local wrapper, never `documentElement`. New primitives live in `src/ui/` styled by CSS classes only (CSP path), tested with Vitest.

**Tech Stack:** React 18 + TS, Vite, Vitest + Testing Library, @fontsource (Inter, JetBrains Mono), eslint flat config.

**Parent spec:** `docs/superpowers/specs/2026-06-11-s37-frontend-redesign-design.md` (§2, §5, §6). Plans 2–4 (customer surface, shell+IA, compositions) are written when their phase starts.

---

### Task 1: Branch, sprint claim, spec commit

**Files:**
- Create: branch `feature/s37-terminal-authority` (off post-PR-#73 main)
- Modify: `docs/SPRINTS.md` (in-flight row)
- Add: `docs/superpowers/specs/2026-06-11-s37-frontend-redesign-design.md` (already on disk, untracked)

- [ ] **Step 1: Verify PR #73 merged, sync main, branch**

```bash
cd ~/Downloads/AURA
gh pr view 73 --json state --jq .state   # must print MERGED — if not, stop and wait
git switch main && git pull --ff-only
git switch -c feature/s37-terminal-authority
```

- [ ] **Step 2: Open the sprint issue**

```bash
gh issue create --title "Sprint S37: Terminal Authority — enterprise frontend redesign" \
  --body "Full redesign program per docs/superpowers/specs/2026-06-11-s37-frontend-redesign-design.md: one design system (dark product + light certificate theme), customer audit surface, auditor-workbench IA re-architecture, FE security hardening. Big-bang branch feature/s37-terminal-authority; phase plans in docs/superpowers/plans/2026-06-11-s37*-*.md." \
  --assignee @me
```

- [ ] **Step 3: Add the in-flight row to docs/SPRINTS.md**

In the `## In flight (active)` table, replace the placeholder row with:

```markdown
| **S37** | Rohith | `feature/s37-terminal-authority` | 2026-06-11 | Terminal Authority enterprise redesign (issue from Step 2): one design system over both surfaces — dark product UI + light certificate theme, auditor-workbench IA, FE security hardening. Spec: `docs/superpowers/specs/2026-06-11-s37-frontend-redesign-design.md`. Big-bang branch; phase plans s37a-d. |
```

- [ ] **Step 4: Commit spec + plan + claim**

```bash
git add docs/superpowers/specs/2026-06-11-s37-frontend-redesign-design.md \
        docs/superpowers/plans/2026-06-11-s37a-design-system-foundation.md \
        docs/SPRINTS.md
git commit -m "docs(s37): redesign spec + s37a plan + sprint claim

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Self-hosted fonts

**Files:**
- Modify: `frontend/package.json` (via npm)
- Modify: `frontend/src/main.tsx:3-5`

- [ ] **Step 1: Install**

```bash
cd ~/Downloads/AURA/frontend
npm install @fontsource/inter @fontsource/jetbrains-mono
```

- [ ] **Step 2: Import weights in `src/main.tsx`** — add directly ABOVE the `./styles/design-system.css` import:

```tsx
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/600.css';
```

- [ ] **Step 3: Verify build + suite**

```bash
npx tsc --noEmit && npx vitest run 2>&1 | tail -2
```
Expected: 192 passed.

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json src/main.tsx
git commit -m "feat(s37a): self-host Inter + JetBrains Mono via fontsource

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `CertificateTheme` wrapper (TDD)

**Files:**
- Create: `frontend/src/ui/CertificateTheme.tsx`
- Test: `frontend/src/ui/__tests__/CertificateTheme.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CertificateTheme } from '../CertificateTheme';

describe('CertificateTheme', () => {
  it('scopes data-theme="certificate" to a wrapper, never documentElement', () => {
    render(<CertificateTheme><p>doc body</p></CertificateTheme>);
    const wrapper = screen.getByText('doc body').closest('[data-theme="certificate"]');
    expect(wrapper).not.toBeNull();
    // The product theme on <html> must be untouched (certificates are
    // a local island — the app around them stays dark).
    expect(document.documentElement.getAttribute('data-theme')).not.toBe('certificate');
  });
});
```

- [ ] **Step 2: Run it — expect FAIL** (`Cannot find module '../CertificateTheme'`)

```bash
npx vitest run src/ui/__tests__/CertificateTheme.test.tsx
```

- [ ] **Step 3: Implement `src/ui/CertificateTheme.tsx`**

```tsx
import React from 'react';

/**
 * Scopes the light, print-safe certificate theme to its children.
 * Certificates are documents — they render light even while the product
 * around them is dark. Never touches documentElement (that belongs to
 * ThemeContext).
 */
export const CertificateTheme: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div data-theme="certificate" className="certificate-root">
    {children}
  </div>
);
```

- [ ] **Step 4: Run test — expect PASS. Then commit**

```bash
npx vitest run src/ui/__tests__/CertificateTheme.test.tsx
git add src/ui
git commit -m "feat(s37a): CertificateTheme local theme island

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: tokens.css — Terminal Authority palette + legacy compatibility layer

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/main.tsx` (import after `design-system.css`)
- Modify: `frontend/src/contexts/ThemeContext.tsx:30` (default `'light'` → `'dark'`)

- [ ] **Step 1: Create `src/styles/tokens.css`** with exactly this content:

```css
/**
 * S37 Terminal Authority tokens.
 * Loads AFTER design-system.css and overrides the color-bearing legacy
 * custom properties — structural tokens (spacing, radii, durations,
 * heights) are theme-neutral and inherited unchanged. This file is the
 * compatibility layer that re-skins unmigrated panels without edits.
 */

:root,
[data-theme='dark'],
[data-theme='light'] {
  /* ── New semantic tokens ─────────────────────────────────────── */
  --signal: #22c55e;          /* verified / success / brand */
  --info: #3b82f6;
  --warn: #f59e0b;
  --danger: #ef4444;
  --bg-raised: #161d27;

  /* ── Typography ──────────────────────────────────────────────── */
  --font-sans: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, 'Cascadia Mono', Consolas, monospace;

  /* ── Surfaces ────────────────────────────────────────────────── */
  --bg-base: #0a0e14;
  --bg-canvas: #0a0e14;
  --bg-primary: #0a0e14;
  --bg-secondary: #0f141b;
  --bg-surface: #0f141b;
  --bg-surface-2: #131a23;
  --bg-elevated: #161d27;
  --bg-tertiary: #161d27;
  --bg-sunken: #070a0f;
  --bg-hover: #18202b;
  --bg-active: #1c2531;
  --bg-selected: rgba(34, 197, 94, 0.08);
  --bg-overlay: rgba(4, 6, 10, 0.72);
  --card-bg: rgba(15, 20, 27, 0.6);

  /* ── Borders ─────────────────────────────────────────────────── */
  --border-default: #1d2530;
  --border-subtle: #161d27;
  --border-hairline: #131a23;
  --border-strong: #2a3543;
  --border-secondary: #1d2530;
  --border-focus: #22c55e;
  --border-brand: #22c55e;
  --border-error: #ef4444;
  --border-success: #22c55e;

  /* ── Text ────────────────────────────────────────────────────── */
  --text-primary: #e6e9ef;
  --text-secondary: #9aa4b2;
  --text-tertiary: #7d8590;

  /* ── Accent (brand = signal green) ───────────────────────────── */
  --accent: #22c55e;
  --accent-hover: #34d56e;
  --accent-active: #1b9e4b;
  --accent-dim: rgba(34, 197, 94, 0.14);
  --accent-glow: rgba(34, 197, 94, 0.35);
  --accent-border: rgba(34, 197, 94, 0.4);

  /* ── Hues + fg aliases ───────────────────────────────────────── */
  --green: #22c55e;  --green-dim: rgba(34, 197, 94, 0.14);  --green-border: rgba(34, 197, 94, 0.4);
  --red: #ef4444;    --red-dim: rgba(239, 68, 68, 0.14);    --red-border: rgba(239, 68, 68, 0.4);
  --yellow: #f59e0b;
  --blue: #3b82f6;   --blue-dim: rgba(59, 130, 246, 0.14);
  --cyan: #22d3ee;   --cyan-dim: rgba(34, 211, 238, 0.14);
  --purple: #a78bfa; --purple-dim: rgba(167, 139, 250, 0.14);
  --fg-accent: #22c55e; --fg-green: #22c55e; --fg-red: #ef4444; --fg-yellow: #f59e0b;
  --fg-blue: #3b82f6; --fg-cyan: #22d3ee; --fg-purple: #a78bfa; --fg-indigo: #818cf8;

  /* ── Charts (Recharts reads these via var()) ─────────────────── */
  --chart-1: #22c55e; --chart-2: #3b82f6; --chart-3: #f59e0b; --chart-4: #a78bfa;
  --chart-5: #22d3ee; --chart-6: #ef4444; --chart-7: #e6e9ef;

  /* ── Scale tokens (legacy *-NNN consumers) ───────────────────── */
  --color-success-50: rgba(34, 197, 94, 0.08);  --color-success-200: rgba(34, 197, 94, 0.3);
  --color-success-500: #22c55e;                 --color-success-600: #16a34a;
  --color-error-50: rgba(239, 68, 68, 0.08);    --color-error-200: rgba(239, 68, 68, 0.3);
  --color-error-500: #ef4444; --color-error-600: #dc2626; --color-error-700: #b91c1c;
  --color-warning-50: rgba(245, 158, 11, 0.08); --color-warning-200: rgba(245, 158, 11, 0.3);
  --color-warning-500: #f59e0b;                 --color-warning-600: #d97706;
  --color-info-50: rgba(59, 130, 246, 0.08);    --color-info-500: #3b82f6;
  --color-primary-50: rgba(34, 197, 94, 0.08);  --color-primary-200: rgba(34, 197, 94, 0.3);
  --color-primary-500: #22c55e;                 --color-primary-600: #16a34a;
  /* Neutral scale INVERTED for dark (legacy code uses low numbers as
     "light bg" — e.g. RadialBar background var(--color-neutral-100)). */
  --color-neutral-0: #0a0e14;  --color-neutral-50: #0f141b;  --color-neutral-100: #1d2530;
  --color-neutral-200: #2a3543; --color-neutral-400: #7d8590; --color-neutral-500: #9aa4b2;
  --color-neutral-600: #b7c0cc; --color-neutral-700: #d3d9e1; --color-neutral-800: #e6e9ef;
  --color-neutral-900: #f4f6f9; --color-neutral-950: #ffffff;

  /* ── Domain status ───────────────────────────────────────────── */
  --cb-closed: #22c55e; --cb-half-open: #f59e0b; --cb-open: #ef4444;

  /* ── Glows ───────────────────────────────────────────────────── */
  --shadow-glow-green: 0 0 0 1px rgba(34, 197, 94, 0.4), 0 0 18px rgba(34, 197, 94, 0.18);
  --shadow-glow-blue: 0 0 0 1px rgba(59, 130, 246, 0.4), 0 0 18px rgba(59, 130, 246, 0.18);
}

/* ── Certificate theme: light, print-safe document island ──────── */
[data-theme='certificate'] {
  --signal: #1a7f3c; --info: #2742ad; --warn: #9a6700; --danger: #b42318;
  --bg-raised: #f3f2ee;

  --bg-base: #fafaf8; --bg-canvas: #fafaf8; --bg-primary: #fafaf8;
  --bg-secondary: #ffffff; --bg-surface: #ffffff; --bg-surface-2: #f6f5f1;
  --bg-elevated: #f3f2ee; --bg-tertiary: #f3f2ee; --bg-sunken: #efeee9;
  --bg-hover: #f1f0eb; --bg-active: #eceae3;
  --bg-selected: rgba(26, 127, 60, 0.08); --bg-overlay: rgba(22, 24, 29, 0.4);
  --card-bg: #ffffff;

  --border-default: #d9d7ce; --border-subtle: #e6e4db; --border-hairline: #efede4;
  --border-strong: #b9b6a9; --border-secondary: #d9d7ce;
  --border-focus: #1a7f3c; --border-brand: #1a7f3c;
  --border-error: #b42318; --border-success: #1a7f3c;

  --text-primary: #16181d; --text-secondary: #3d4148; --text-tertiary: #5c5f66;

  --accent: #1a7f3c; --accent-hover: #16692f; --accent-active: #125627;
  --accent-dim: rgba(26, 127, 60, 0.1); --accent-glow: rgba(26, 127, 60, 0.25);
  --accent-border: rgba(26, 127, 60, 0.4);

  --green: #1a7f3c; --green-dim: rgba(26, 127, 60, 0.1); --green-border: rgba(26, 127, 60, 0.4);
  --red: #b42318; --red-dim: rgba(180, 35, 24, 0.1); --red-border: rgba(180, 35, 24, 0.4);
  --yellow: #9a6700; --blue: #2742ad; --blue-dim: rgba(39, 66, 173, 0.1);
  --cyan: #0e7490; --cyan-dim: rgba(14, 116, 144, 0.1);
  --purple: #6d28d9; --purple-dim: rgba(109, 40, 217, 0.1);
  --fg-accent: #1a7f3c; --fg-green: #1a7f3c; --fg-red: #b42318; --fg-yellow: #9a6700;
  --fg-blue: #2742ad; --fg-cyan: #0e7490; --fg-purple: #6d28d9; --fg-indigo: #4338ca;

  --chart-1: #1a7f3c; --chart-2: #2742ad; --chart-3: #9a6700; --chart-4: #6d28d9;
  --chart-5: #0e7490; --chart-6: #b42318; --chart-7: #16181d;

  --color-neutral-0: #ffffff; --color-neutral-50: #fafaf8; --color-neutral-100: #efede4;
  --color-neutral-200: #d9d7ce; --color-neutral-400: #8a8d94; --color-neutral-500: #5c5f66;
  --color-neutral-600: #3d4148; --color-neutral-700: #2b2e34; --color-neutral-800: #16181d;
  --color-neutral-900: #0f1115; --color-neutral-950: #000000;

  --shadow-glow-green: 0 0 0 1px rgba(26, 127, 60, 0.35);
  --shadow-glow-blue: 0 0 0 1px rgba(39, 66, 173, 0.35);
}

.certificate-root {
  background: var(--bg-base);
  color: var(--text-primary);
}

@media print {
  .certificate-root { background: #ffffff; }
}
```

- [ ] **Step 2: Import in `src/main.tsx`** directly AFTER `./styles/components.css`:

```tsx
import './styles/tokens.css';
```

- [ ] **Step 3: Flip the product default to dark** — `src/contexts/ThemeContext.tsx` line 30:

```tsx
    return savedTheme || 'dark';
```

- [ ] **Step 4: Full suite + lint + types**

```bash
npx vitest run 2>&1 | tail -2 && npx tsc --noEmit && npx eslint src --max-warnings 0
```
Expected: 193 passed (192 + Task 3), clean.

- [ ] **Step 5: Visual checkpoint** — with `npx vite --port 5173` running, screenshot `/`, `/app`, `/audit/new` via devtools; confirm green-accent reskin everywhere and no unreadable text. Fix any token value that produces broken contrast before committing.

- [ ] **Step 6: Commit**

```bash
git add src/styles/tokens.css src/main.tsx src/contexts/ThemeContext.tsx
git commit -m "feat(s37a): Terminal Authority tokens + legacy compat layer + certificate theme

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `Button` primitive (TDD)

**Files:**
- Create: `frontend/src/ui/Button.tsx`, `frontend/src/ui/primitives.css`
- Modify: `frontend/src/main.tsx` (import `./ui/primitives.css` after tokens.css)
- Test: `frontend/src/ui/__tests__/Button.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Button } from '../Button';

describe('ui/Button', () => {
  it('renders variants as classes (CSS-only styling — CSP path)', () => {
    render(<Button variant="danger">Revoke</Button>);
    const btn = screen.getByRole('button', { name: 'Revoke' });
    expect(btn.className).toContain('ui-btn');
    expect(btn.className).toContain('ui-btn--danger');
    expect(btn.getAttribute('style')).toBeNull();
  });

  it('disabled keeps a visible affordance class and blocks clicks', () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Next</Button>);
    const btn = screen.getByRole('button', { name: 'Next' });
    expect(btn).toBeDisabled();
    btn.click();
    expect(onClick).not.toHaveBeenCalled();
  });

  it('defaults to primary, supports size', () => {
    render(<Button size="sm">Go</Button>);
    const btn = screen.getByRole('button', { name: 'Go' });
    expect(btn.className).toContain('ui-btn--primary');
    expect(btn.className).toContain('ui-btn--sm');
  });
});
```

- [ ] **Step 2: Run — expect FAIL** (module not found)

```bash
npx vitest run src/ui/__tests__/Button.test.tsx
```

- [ ] **Step 3: Implement `src/ui/Button.tsx`**

```tsx
import React from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  className,
  type = 'button',
  children,
  ...rest
}) => (
  <button
    type={type}
    className={['ui-btn', `ui-btn--${variant}`, `ui-btn--${size}`, className]
      .filter(Boolean)
      .join(' ')}
    {...rest}
  >
    {children}
  </button>
);
```

- [ ] **Step 4: Create `src/ui/primitives.css`** (Button section now; later tasks append):

```css
/* S37 primitives — classes only, themed entirely by tokens.css vars. */

.ui-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-family: var(--font-sans);
  font-weight: 600;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  cursor: pointer;
  transition: background var(--dur-fast), border-color var(--dur-fast), color var(--dur-fast);
}
.ui-btn--sm { font-size: var(--font-xs); padding: var(--space-1) var(--space-3); min-height: var(--btn-height-sm); }
.ui-btn--md { font-size: var(--font-sm); padding: var(--space-2) var(--space-5); min-height: var(--btn-height-md); }
.ui-btn--lg { font-size: var(--font-md); padding: var(--space-3) var(--space-6); min-height: var(--btn-height-lg); }

.ui-btn--primary { background: var(--accent); color: #06270f; }
.ui-btn--primary:hover:not(:disabled) { background: var(--accent-hover); }
.ui-btn--primary:active:not(:disabled) { background: var(--accent-active); }

.ui-btn--secondary { background: transparent; color: var(--text-primary); border-color: var(--border-strong); }
.ui-btn--secondary:hover:not(:disabled) { border-color: var(--accent-border); color: var(--accent); }

.ui-btn--ghost { background: transparent; color: var(--text-secondary); }
.ui-btn--ghost:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }

.ui-btn--danger { background: transparent; color: var(--danger); border-color: var(--red-border); }
.ui-btn--danger:hover:not(:disabled) { background: var(--red-dim); }

/* Disabled stays VISIBLE: outline + muted text, never a dark-on-dark fill. */
.ui-btn:disabled { background: transparent; color: var(--text-tertiary); border-color: var(--border-default); cursor: not-allowed; }

.ui-btn:focus-visible { outline: 2px solid var(--border-focus); outline-offset: 2px; }
```

- [ ] **Step 5: Import in `src/main.tsx`** after tokens.css: `import './ui/primitives.css';`

- [ ] **Step 6: Run test — PASS; then commit**

```bash
npx vitest run src/ui/__tests__/Button.test.tsx
git add src/ui src/main.tsx
git commit -m "feat(s37a): Button primitive + primitives.css

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `Card` primitive (TDD)

**Files:**
- Create: `frontend/src/ui/Card.tsx`
- Modify: `frontend/src/ui/primitives.css` (append)
- Test: `frontend/src/ui/__tests__/Card.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Card } from '../Card';

describe('ui/Card', () => {
  it('renders header/body composition with classes only', () => {
    render(
      <Card title="Service health" subtitle="live">
        <p>body content</p>
      </Card>,
    );
    expect(screen.getByRole('heading', { name: 'Service health' })).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
    const card = screen.getByText('body content').closest('.ui-card');
    expect(card).not.toBeNull();
  });

  it('supports an evidence accent edge', () => {
    render(<Card accent="warn">flagged</Card>);
    expect(screen.getByText('flagged').closest('.ui-card')!.className).toContain('ui-card--warn');
  });
});
```

- [ ] **Step 2: Run — FAIL.** `npx vitest run src/ui/__tests__/Card.test.tsx`

- [ ] **Step 3: Implement `src/ui/Card.tsx`**

```tsx
import React from 'react';

export interface CardProps {
  title?: string;
  subtitle?: string;
  /** Evidentiary accent-left edge (3px) — signal/warn/danger/info. */
  accent?: 'signal' | 'warn' | 'danger' | 'info';
  actions?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}

export const Card: React.FC<CardProps> = ({ title, subtitle, accent, actions, className, children }) => (
  <section className={['ui-card', accent && `ui-card--${accent}`, className].filter(Boolean).join(' ')}>
    {(title || actions) && (
      <header className="ui-card__header">
        <div>
          {title && <h3 className="ui-card__title">{title}</h3>}
          {subtitle && <p className="ui-card__subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="ui-card__actions">{actions}</div>}
      </header>
    )}
    <div className="ui-card__body">{children}</div>
  </section>
);
```

- [ ] **Step 4: Append to `primitives.css`**

```css
.ui-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
}
.ui-card--signal { border-left: 3px solid var(--signal); }
.ui-card--warn   { border-left: 3px solid var(--warn); }
.ui-card--danger { border-left: 3px solid var(--danger); }
.ui-card--info   { border-left: 3px solid var(--info); }
.ui-card__header {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border-subtle);
}
.ui-card__title { margin: 0; font-size: var(--font-md); font-weight: 600; color: var(--text-primary); }
.ui-card__subtitle { margin: var(--space-1) 0 0; font-size: var(--font-xs); color: var(--text-tertiary); }
.ui-card__actions { display: flex; gap: var(--space-2); }
.ui-card__body { padding: var(--space-5); }
```

- [ ] **Step 5: PASS + commit**

```bash
npx vitest run src/ui/__tests__/Card.test.tsx
git add src/ui
git commit -m "feat(s37a): Card primitive

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: `Badge` + `StatusGlyph` (TDD)

**Files:**
- Create: `frontend/src/ui/Badge.tsx`
- Modify: `frontend/src/ui/primitives.css` (append)
- Test: `frontend/src/ui/__tests__/Badge.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Badge, STATUS_GLYPHS } from '../Badge';

describe('ui/Badge', () => {
  it('always pairs color with a glyph — color is never the only signal', () => {
    for (const status of ['verified', 'pending', 'warn', 'danger', 'neutral'] as const) {
      const { unmount } = render(<Badge status={status}>{status}</Badge>);
      const badge = screen.getByText(status).closest('.ui-badge')!;
      expect(badge.className).toContain(`ui-badge--${status}`);
      expect(badge.textContent).toContain(STATUS_GLYPHS[status]);
      unmount();
    }
  });
});
```

- [ ] **Step 2: Run — FAIL.** `npx vitest run src/ui/__tests__/Badge.test.tsx`

- [ ] **Step 3: Implement `src/ui/Badge.tsx`**

```tsx
import React from 'react';

export type BadgeStatus = 'verified' | 'pending' | 'warn' | 'danger' | 'neutral';

/** Glyph per status — a11y: color is never the only signal. */
export const STATUS_GLYPHS: Record<BadgeStatus, string> = {
  verified: '✓',
  pending: '◷',
  warn: '⚠',
  danger: '✕',
  neutral: '▪',
};

export const Badge: React.FC<{ status: BadgeStatus; children: React.ReactNode }> = ({ status, children }) => (
  <span className={`ui-badge ui-badge--${status}`}>
    <span aria-hidden="true" className="ui-badge__glyph">{STATUS_GLYPHS[status]}</span>
    {children}
  </span>
);
```

- [ ] **Step 4: Append to `primitives.css`**

```css
.ui-badge {
  display: inline-flex; align-items: center; gap: var(--space-1);
  font-family: var(--font-sans); font-size: var(--font-2xs); font-weight: 600;
  letter-spacing: 0.04em; text-transform: uppercase;
  padding: 2px var(--space-2); border-radius: var(--radius-full);
  border: 1px solid;
}
.ui-badge--verified { color: var(--signal); border-color: var(--green-border); background: var(--green-dim); }
.ui-badge--pending  { color: var(--text-secondary); border-color: var(--border-strong); background: var(--bg-raised); }
.ui-badge--warn     { color: var(--warn); border-color: rgba(245, 158, 11, 0.4); background: rgba(245, 158, 11, 0.12); }
.ui-badge--danger   { color: var(--danger); border-color: var(--red-border); background: var(--red-dim); }
.ui-badge--neutral  { color: var(--text-tertiary); border-color: var(--border-default); background: transparent; }
```

- [ ] **Step 5: PASS + commit**

```bash
npx vitest run src/ui/__tests__/Badge.test.tsx
git add src/ui
git commit -m "feat(s37a): Badge with mandatory status glyphs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: `HashChip` (TDD)

**Files:**
- Create: `frontend/src/ui/HashChip.tsx`
- Modify: `frontend/src/ui/primitives.css` (append)
- Test: `frontend/src/ui/__tests__/HashChip.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HashChip } from '../HashChip';

const HASH = 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90';

describe('ui/HashChip', () => {
  it('middle-truncates and copies the FULL hash', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<HashChip hash={HASH} />);
    expect(screen.getByText('a1b2c3d4…7e8f90')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith(HASH);
  });

  it('renders a verify link only for a sanitized hash', () => {
    render(<HashChip hash={HASH} verifyHref={`/verify/${HASH}`} />);
    expect(screen.getByRole('link', { name: /verify/i })).toHaveAttribute('href', `/verify/${HASH}`);
  });

  it('refuses to render a link for a non-hex hash (Sec-6 boundary)', () => {
    render(<HashChip hash={'javascript:alert(1)'} verifyHref="/verify/javascript:alert(1)" />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText('invalid hash')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — FAIL.** `npx vitest run src/ui/__tests__/HashChip.test.tsx`

- [ ] **Step 3: Implement `src/ui/HashChip.tsx`**

```tsx
import React from 'react';

import { sanitizeRecordHash } from '../services/api';

export interface HashChipProps {
  hash: string;
  /** Optional verify link — rendered ONLY when the hash passes the
   * Sec-6 hex gate; the chip is a sanctioned sink for remote hashes. */
  verifyHref?: string;
}

export const HashChip: React.FC<HashChipProps> = ({ hash, verifyHref }) => {
  const clean = sanitizeRecordHash(hash);
  if (!clean) {
    return <span className="ui-hashchip ui-hashchip--invalid">invalid hash</span>;
  }
  const short = `${clean.slice(0, 8)}…${clean.slice(-6)}`;
  return (
    <span className="ui-hashchip">
      <code className="ui-hashchip__hash" title={clean}>{short}</code>
      <button
        type="button"
        className="ui-hashchip__copy"
        aria-label="Copy full hash"
        onClick={() => { void navigator.clipboard?.writeText(clean); }}
      >
        ⧉
      </button>
      {verifyHref && (
        <a className="ui-hashchip__verify" href={verifyHref}>verify</a>
      )}
    </span>
  );
};
```

- [ ] **Step 4: Append to `primitives.css`**

```css
.ui-hashchip {
  display: inline-flex; align-items: center; gap: var(--space-2);
  font-family: var(--font-mono); font-size: var(--font-xs);
  background: var(--bg-raised); border: 1px solid var(--border-default);
  border-radius: var(--radius-sm); padding: 2px var(--space-2);
  color: var(--text-secondary);
}
.ui-hashchip__hash { color: var(--text-primary); }
.ui-hashchip__copy {
  background: none; border: none; cursor: pointer;
  color: var(--text-tertiary); font-size: var(--font-xs); padding: 0;
}
.ui-hashchip__copy:hover { color: var(--accent); }
.ui-hashchip__verify { color: var(--signal); text-decoration: underline; }
.ui-hashchip--invalid { color: var(--danger); font-family: var(--font-sans); }
```

- [ ] **Step 5: PASS + commit**

```bash
npx vitest run src/ui/__tests__/HashChip.test.tsx
git add src/ui
git commit -m "feat(s37a): HashChip — sanctioned sink for remote hashes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: `Stepper` (TDD)

**Files:**
- Create: `frontend/src/ui/Stepper.tsx`
- Modify: `frontend/src/ui/primitives.css` (append)
- Test: `frontend/src/ui/__tests__/Stepper.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Stepper } from '../Stepper';

describe('ui/Stepper', () => {
  it('marks done/current/todo states and exposes progress to AT', () => {
    render(<Stepper steps={['Upload', 'Map', 'Review']} current={1} />);
    const list = screen.getByRole('list');
    expect(list.className).toContain('ui-stepper');
    expect(screen.getByText('Upload').closest('li')!.className).toContain('ui-step--done');
    const current = screen.getByText('Map').closest('li')!;
    expect(current.className).toContain('ui-step--current');
    expect(current.getAttribute('aria-current')).toBe('step');
    expect(screen.getByText('Review').closest('li')!.className).toContain('ui-step--todo');
  });
});
```

- [ ] **Step 2: Run — FAIL.** `npx vitest run src/ui/__tests__/Stepper.test.tsx`

- [ ] **Step 3: Implement `src/ui/Stepper.tsx`**

```tsx
import React from 'react';

export const Stepper: React.FC<{ steps: string[]; current: number }> = ({ steps, current }) => (
  <ol className="ui-stepper">
    {steps.map((label, i) => {
      const state = i < current ? 'done' : i === current ? 'current' : 'todo';
      return (
        <li
          key={label}
          className={`ui-step ui-step--${state}`}
          aria-current={state === 'current' ? 'step' : undefined}
        >
          <span aria-hidden="true" className="ui-step__marker">
            {state === 'done' ? '✓' : i + 1}
          </span>
          <span className="ui-step__label">{label}</span>
        </li>
      );
    })}
  </ol>
);
```

- [ ] **Step 4: Append to `primitives.css`**

```css
.ui-stepper { display: flex; gap: var(--space-5); list-style: none; margin: 0; padding: 0; }
.ui-step { display: flex; align-items: center; gap: var(--space-2); font-size: var(--font-xs); }
.ui-step__marker {
  display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; border-radius: var(--radius-full);
  font-family: var(--font-mono); font-size: var(--font-2xs); font-weight: 700;
  border: 1px solid var(--border-strong); color: var(--text-tertiary);
}
.ui-step--done .ui-step__marker { background: var(--green-dim); border-color: var(--green-border); color: var(--signal); }
.ui-step--current .ui-step__marker { border-color: var(--accent); color: var(--accent); }
.ui-step--current .ui-step__label { color: var(--text-primary); font-weight: 600; }
.ui-step--done .ui-step__label, .ui-step--todo .ui-step__label { color: var(--text-tertiary); }
```

- [ ] **Step 5: PASS + commit**

```bash
npx vitest run src/ui/__tests__/Stepper.test.tsx
git add src/ui
git commit -m "feat(s37a): Stepper primitive

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: eslint security guards

**Files:**
- Modify: `frontend/eslint.config.js:22-32` (rules block)

- [ ] **Step 1: Add to the `rules` object in `eslint.config.js`** (after the no-unused-vars entry):

```js
      // S37 security guards.
      'no-restricted-syntax': ['error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: 'dangerouslySetInnerHTML is banned — render text, or extend a sanctioned primitive (HashChip pattern).',
        },
        {
          selector: "JSXOpeningElement[name.name='a']:has(JSXAttribute[name.name='target'][value.value='_blank']):not(:has(JSXAttribute[name.name='rel']))",
          message: 'target="_blank" requires rel="noopener noreferrer" (reverse tabnabbing).',
        },
      ],
```

- [ ] **Step 2: Prove the guard fires** — create a throwaway violation, watch eslint fail, delete it:

```bash
cat > src/_guardcheck.tsx <<'EOF'
export const Bad = () => <div dangerouslySetInnerHTML={{ __html: 'x' }} />;
EOF
npx eslint src/_guardcheck.tsx   # Expected: 1 error mentioning "banned"
rm src/_guardcheck.tsx
```

- [ ] **Step 3: Verify the real tree is clean, then commit**

```bash
npx eslint src --max-warnings 0
git add eslint.config.js
git commit -m "chore(s37a): eslint guards — ban dangerouslySetInnerHTML, enforce noopener

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: CSP meta + final verification + push

**Files:**
- Modify: `frontend/index.html` (head)

- [ ] **Step 1: Add the CSP meta** to `index.html` `<head>`, above the title:

```html
    <!-- S37 §5.2: strict-by-default CSP. style-src keeps 'unsafe-inline'
         until legacy inline-styled panels migrate (tracked in the spec);
         connect-src allows the dev gateway + Vite HMR websocket. -->
    <meta http-equiv="Content-Security-Policy"
          content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' http://localhost:8000 ws: wss:; object-src 'none'; base-uri 'self'; form-action 'self'" />
```

- [ ] **Step 2: Boot the app and verify nothing is CSP-blocked**

```bash
npx vite --port 5173 --strictPort   # background
```
Open `/`, `/app`, `/audit/new` via devtools; check console for CSP violation reports. Sentry note: if `VITE_SENTRY_DSN` is set, its ingest origin must be added to `connect-src` — leave a comment, do not widen by default.

- [ ] **Step 3: Full suite, lint, types**

```bash
npx vitest run 2>&1 | tail -2 && npx tsc --noEmit && npx eslint src --max-warnings 0
```
Expected: 198+ passed (192 + 6 new primitive test files), clean.

- [ ] **Step 4: Visual checkpoint screenshots** (devtools): landing, dashboard, wizard, certificate page — attach observations; fix contrast regressions before committing.

- [ ] **Step 5: Commit + push the branch**

```bash
git add index.html
git commit -m "feat(s37a): strict-default CSP meta

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin feature/s37-terminal-authority
```

No PR yet — the big-bang branch accumulates phases s37a–d; PR opens when the program is demo-complete (spec §7).

---

## Self-Review

- **Spec coverage:** §2 tokens/themes/fonts → Tasks 2–4; §2 primitives subset for Plan 2 → Tasks 5–9 (DataTable/Drawer/KpiTile/ChartFrame/EmptyState deliberately deferred to the plans that first consume them — YAGNI); §5.2 CSP → Task 11; §5.4 lint guards → Task 10; §5.3 sanitizer reuse → Task 8. §3/§4/§5.1 are Plans 2–4.
- **Placeholders:** none — every code step carries full content.
- **Type consistency:** `BadgeStatus` glyph map exported as `STATUS_GLYPHS` and tested under that name; `sanitizeRecordHash` import path matches `src/services/api`; class prefixes `ui-` consistent across css/tests.
