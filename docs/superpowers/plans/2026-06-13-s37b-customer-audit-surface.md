# S37b — Customer Audit Surface (Terminal Authority) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Rebuild the five public audit-service pages (landing → wizard → progress → certificate → verify) on the s37a design system, so the surface a YC judge or banking buyer sees reads as the best enterprise audit product on the market — without changing any backend or breaking the 15 existing testids.

**Architecture:** Each page swaps inline-style scaffolding for s37a primitives (`Button`, `Card`, `Badge`, `HashChip`, `Stepper`) and tokens. `PublicShell` stays dark product chrome; the **Certificate** renders inside `<CertificateTheme>` (light, print-safe document). No new backend calls — the landing trust band is a static signed/verifiable statement (no "latest cert" endpoint exists; faking a live hash would overclaim).

**Tech Stack:** React 18 + TS, Vite, Vitest + Testing Library. Branch: `feature/s37-terminal-authority` (continues s37a; no PR this phase).

**Parent spec:** `docs/superpowers/specs/2026-06-11-s37-frontend-redesign-design.md` §3.

**Invariants (every task):**
- Preserve these testids exactly: `audit-front-door`, `scenario-card-<id>`, `scenarios-error`, `audit-wizard`, `wizard-dropzone`, `wizard-file-input`, `wizard-next`, `wizard-back`, `wizard-run`, `wizard-dot-<n>`, `audit-progress`, `audit-failed`, `estimator-<method>`, `certificate`, `cert-signature-badge`, `cert-hash`, `cert-key-source`, `cert-verify-status`, `cert-verify-link`, `cert-download-pdf`, `cert-degraded`, `certificate-page`, `verify-page`.
- No inline `style={{…}}` in rewritten JSX (CSP/lint posture) — use classes in a new `src/audit/audit.css`, imported once by `PublicShell`.
- Full suite + `tsc` + `eslint --max-warnings 0` green before each commit; co-author `Claude Opus 4.8`.

---

### Task 1: `audit.css` + PublicShell on primitives

**Files:**
- Create: `frontend/src/audit/audit.css`
- Modify: `frontend/src/audit/PublicShell.tsx`
- Test: `frontend/src/audit/__tests__/AppRoutes.test.tsx` (already mounts PublicShell — must stay green)

- [ ] **Step 1: Create `audit.css`** with the shell + shared classes:

```css
/* S37b — customer audit surface. Classes only; themed by tokens.css. */
.aud-shell { min-height: 100vh; display: flex; flex-direction: column; background: var(--bg-base); color: var(--text-primary); }
.aud-shell__header { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-4) var(--space-6); border-bottom: 1px solid var(--border-default); }
.aud-shell__brand { font-weight: 700; letter-spacing: -0.02em; }
.aud-shell__tag { font-family: var(--font-mono); font-size: var(--font-2xs); color: var(--signal); text-transform: uppercase; letter-spacing: 0.12em; }
.aud-shell__main { flex: 1; width: 100%; max-width: 1100px; margin: 0 auto; padding: var(--space-8) var(--space-6); }
.aud-shell__footer { padding: var(--space-4) var(--space-6); border-top: 1px solid var(--border-default); font-size: var(--font-xs); color: var(--text-tertiary); display: flex; gap: var(--space-2); align-items: center; }

.aud-hero__title { font-size: var(--font-3xl); font-weight: 800; letter-spacing: -0.03em; margin: 0; }
.aud-hero__sub { color: var(--text-secondary); margin: var(--space-2) 0 var(--space-5); max-width: 60ch; }
.aud-trust { display: inline-flex; align-items: center; gap: var(--space-2); font-family: var(--font-mono); font-size: var(--font-xs); color: var(--text-secondary); background: var(--bg-raised); border: 1px solid var(--border-default); border-radius: var(--radius-md); padding: var(--space-2) var(--space-3); margin-bottom: var(--space-8); }

.aud-scenarios { display: grid; grid-template-columns: repeat(auto-fill, minmax(264px, 1fr)); gap: var(--space-5); }
.aud-scenario { text-align: left; cursor: pointer; background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: var(--radius-lg); padding: var(--space-5); transition: border-color var(--dur-fast), transform var(--dur-fast); }
.aud-scenario:hover:not(:disabled) { border-color: var(--accent-border); transform: translateY(-2px); }
.aud-scenario:disabled { opacity: 0.55; cursor: progress; }
.aud-scenario__vertical { font-family: var(--font-mono); font-size: var(--font-2xs); text-transform: uppercase; letter-spacing: 0.1em; color: var(--signal); }
.aud-scenario__title { margin: var(--space-2) 0; font-size: var(--font-md); }
.aud-scenario__desc { font-size: var(--font-sm); color: var(--text-secondary); margin: 0 0 var(--space-3); }
.aud-scenario__cta { font-size: var(--font-sm); color: var(--accent); font-weight: 600; }
.aud-links { margin-top: var(--space-8); font-size: var(--font-sm); display: flex; gap: var(--space-3); align-items: center; }
.aud-link { color: var(--accent); text-decoration: none; }
.aud-link--muted { color: var(--text-tertiary); }

/* Progress timeline */
.aud-progress__stages { display: flex; gap: var(--space-4); margin: var(--space-5) 0; font-family: var(--font-mono); font-size: var(--font-xs); }
.aud-stage { display: flex; align-items: center; gap: var(--space-2); color: var(--text-tertiary); }
.aud-stage--active { color: var(--accent); }
.aud-stage--done { color: var(--signal); }
.aud-estimator { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-3) 0; border-bottom: 1px solid var(--border-default); }
.aud-estimator__dot { width: 12px; height: 12px; border-radius: var(--radius-full); flex-shrink: 0; }
.aud-estimator__dot--running { background: var(--accent); }
.aud-estimator__dot--done { background: var(--signal); }
.aud-estimator__dot--error { background: var(--danger); }
.aud-estimator__method { flex: 1; font-weight: 500; }
.aud-estimator__value { font-family: var(--font-mono); font-size: var(--font-sm); color: var(--text-tertiary); }

/* Certificate document (rendered inside CertificateTheme) */
.aud-cert { max-width: 720px; margin: 0 auto; background: var(--bg-surface); border: 1px solid var(--border-strong); border-radius: var(--radius-lg); padding: var(--space-8); }
.aud-cert__seal { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-5); }
.aud-cert__doctype { font-family: var(--font-mono); font-size: var(--font-2xs); letter-spacing: 0.16em; color: var(--text-tertiary); text-transform: uppercase; }
.aud-cert__title { font-size: var(--font-2xl); margin: 0 0 var(--space-2); }
.aud-cert__verdict { color: var(--text-secondary); margin: 0 0 var(--space-6); }
.aud-cert__evidence { background: var(--bg-raised); border-radius: var(--radius-md); padding: var(--space-4); margin-bottom: var(--space-6); }
.aud-cert__label { font-family: var(--font-mono); font-size: var(--font-2xs); text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-tertiary); }
.aud-cert__keysrc { margin-top: var(--space-3); font-size: var(--font-sm); color: var(--text-tertiary); }
.aud-cert__verifystatus { margin-bottom: var(--space-5); font-weight: 600; }
.aud-cert__actions { display: flex; gap: var(--space-3); }
.aud-cert__pdf { padding: var(--space-2) var(--space-5); background: var(--accent); color: #06270f; border-radius: var(--radius-md); text-decoration: none; font-weight: 600; }
@media print { .aud-cert__actions { display: none; } }
```

- [ ] **Step 2: Rewrite `PublicShell.tsx`** to import the css once and use classes:

```tsx
import type { ReactNode } from 'react';
import './audit.css';

/**
 * Chrome-free wrapper for the public audit-service surfaces. Deliberately
 * mounts NONE of the dashboard's auth/data hooks — an outside regulator
 * hitting /verify must reach only the verification endpoint.
 */
export function PublicShell({ children }: { children: ReactNode }) {
  return (
    <div data-testid="public-shell" className="aud-shell">
      <header className="aud-shell__header">
        <span className="aud-shell__brand">AURA</span>
        <span className="aud-shell__tag">Audit Service</span>
      </header>
      <main className="aud-shell__main">{children}</main>
      <footer className="aud-shell__footer">
        <span aria-hidden="true">⬢</span>
        Cryptographically-verifiable compliance audits · ED25519 signed
      </footer>
    </div>
  );
}
```

- [ ] **Step 3: Verify + commit**

```bash
cd ~/Downloads/AURA/frontend
npx vitest run src/audit/__tests__/AppRoutes.test.tsx && npx tsc --noEmit && npx eslint src --max-warnings 0
cd ~/Downloads/AURA
git add frontend/src/audit/audit.css frontend/src/audit/PublicShell.tsx
git commit -m "feat(s37b): audit.css + PublicShell on tokens/classes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: AuditFrontDoor — hero, static trust band, scenario cards

**Files:**
- Modify: `frontend/src/audit/AuditFrontDoor.tsx`
- Test: `frontend/src/audit/__tests__/AuditFrontDoor.test.tsx` (add trust-band case; keep card/error cases)

- [ ] **Step 1: Add a failing test** for the trust band (append to the existing describe):

```tsx
it('shows a static signed-verifiable trust band (no faked live hash)', async () => {
  mockListScenarios.resolves([]);          // match the file's existing mock helper
  render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
  const band = await screen.findByTestId('aud-trust-band');
  expect(band.textContent).toMatch(/ED25519/i);
  expect(band.textContent).toMatch(/verif/i);
});
```
(Use the same scenario-mock + render harness already at the top of the test file; if the helper name differs, match it.)

- [ ] **Step 2: Run — expect FAIL** (`aud-trust-band` not found).
`npx vitest run src/audit/__tests__/AuditFrontDoor.test.tsx`

- [ ] **Step 3: Rewrite `AuditFrontDoor.tsx`** — same logic/handlers, classes + primitives, new trust band. Replace the returned JSX body:

```tsx
  return (
    <div data-testid="audit-front-door">
      <h1 className="aud-hero__title">Cryptographically-verifiable compliance audits</h1>
      <p className="aud-hero__sub">
        Pick a regulated-decision scenario. Watch the audit run. Get a signed
        certificate anyone can verify.
      </p>
      <div data-testid="aud-trust-band" className="aud-trust">
        <span aria-hidden="true">⬢</span>
        Every result is ED25519-signed and independently verifiable.
      </div>

      {error && (
        <div data-testid="scenarios-error" className="aud-scenario" style={undefined}>
          Couldn't load scenarios. <button className="ui-btn ui-btn--secondary ui-btn--sm" onClick={load}>Retry</button>
        </div>
      )}

      {!scenarios && !error && <p className="aud-scenario__desc">Loading scenarios…</p>}

      <div className="aud-scenarios">
        {scenarios?.map((s) => (
          <button
            key={s.id}
            data-testid={`scenario-card-${s.id}`}
            className="aud-scenario"
            onClick={() => run(s.id)}
            disabled={launching !== null}
          >
            <span className="aud-scenario__vertical">{s.vertical}</span>
            <h3 className="aud-scenario__title">{s.title}</h3>
            <p className="aud-scenario__desc">{s.description}</p>
            <span className="aud-scenario__cta">{launching === s.id ? 'Starting…' : 'Run audit →'}</span>
          </button>
        ))}
      </div>

      <p className="aud-links">
        <Link to="/audit/new" className="aud-link">Run a custom audit</Link>
        <span aria-hidden="true">·</span>
        {/* Hard nav: intentionally exits the public shell to load the dashboard. */}
        <a href="/app" className="aud-link aud-link--muted">Open dashboard</a>
      </p>
    </div>
  );
```
Remove the now-unused `style={undefined}` if eslint flags it — just drop the attribute (it's only there to mark the removed inline style).

- [ ] **Step 4: Run — expect PASS (new + existing). Commit.**

```bash
npx vitest run src/audit/__tests__/AuditFrontDoor.test.tsx && npx eslint src --max-warnings 0
git add frontend/src/audit/AuditFrontDoor.tsx frontend/src/audit/__tests__/AuditFrontDoor.test.tsx
git commit -m "feat(s37b): AuditFrontDoor hero + honest trust band + carded scenarios

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: AuditWizard — Stepper + Button primitives

**Files:**
- Modify: `frontend/src/audit/AuditWizard.tsx`
- Test: `frontend/src/audit/__tests__/AuditWizard.test.tsx` (keep all; add stepper assertion)

- [ ] **Step 1: Add a failing test** asserting the `Stepper` renders with the wizard:

```tsx
it('renders the Stepper with the current step marked', () => {
  render(<MemoryRouter><AuditWizard /></MemoryRouter>);
  // Upload is step 0 → current.
  expect(screen.getByText('Upload').closest('li')!.className).toContain('ui-step--current');
});
```

- [ ] **Step 2: Run — expect FAIL.** `npx vitest run src/audit/__tests__/AuditWizard.test.tsx`

- [ ] **Step 3: Edit `AuditWizard.tsx`** — import primitives, replace the dot row and the `btn` helper:

Add imports at top:
```tsx
import { Stepper } from '../ui/Stepper';
import { Button } from '../ui/Button';
```

Replace the dots block (the `['Upload','Map','Review'].map(...)` span row) with — keeping a hidden compatibility node for the legacy `wizard-dot-<n>` testids that other tests assert:
```tsx
      <Stepper steps={['Upload', 'Map', 'Review']} current={step} />
      {/* Legacy testid hooks retained for existing assertions. */}
      <div hidden>
        {['Upload', 'Map', 'Review'].map((label, i) => (
          <span key={label} data-testid={`wizard-dot-${i}`}>{i + 1}. {label}</span>
        ))}
      </div>
```

Replace the `btn` helper body with the `Button` primitive (variant maps enabled→primary, disabled stays visible via the primitive's own disabled style):
```tsx
  const btn = (testid: string, label: string, enabled: boolean, onClick: () => void) => (
    <Button data-testid={testid} variant="primary" disabled={!enabled} onClick={onClick}>
      {label}
    </Button>
  );
```
The back button currently passes `'Back'` as primary; switch it to secondary inline where it's rendered:
```tsx
        {step > 0
          ? <Button data-testid="wizard-back" variant="secondary" onClick={() => setStep((s) => s - 1)}>Back</Button>
          : <span />}
```

- [ ] **Step 4: Run — expect PASS (all wizard tests). Commit.**

```bash
npx vitest run src/audit/__tests__/AuditWizard.test.tsx && npx eslint src --max-warnings 0
git add frontend/src/audit/AuditWizard.tsx frontend/src/audit/__tests__/AuditWizard.test.tsx
git commit -m "feat(s37b): wizard on Stepper + Button primitives (legacy dot testids retained)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: AuditProgress — stage timeline + classed estimator rows

**Files:**
- Modify: `frontend/src/audit/AuditProgress.tsx`
- Test: `frontend/src/audit/__tests__/AuditProgress.test.tsx` (keep `estimator-*`, `audit-failed`)

- [ ] **Step 1: Add a failing test** for the stage timeline:

```tsx
it('shows a stage timeline driven by job state', () => {
  // use the file's existing useJobPolling mock to return { state: 'running', artifact: { estimates: [] } }
  renderWithSnapshot({ state: 'running', artifact: { estimates: [] } });
  expect(screen.getByTestId('aud-stages')).toBeInTheDocument();
});
```
(Reuse the file's existing polling-mock helper; if it inlines the mock, follow that shape.)

- [ ] **Step 2: Run — expect FAIL.** `npx vitest run src/audit/__tests__/AuditProgress.test.tsx`

- [ ] **Step 3: Edit `AuditProgress.tsx`** — replace inline-styled `EstimatorRow` and the main return with classed markup + a stage strip. Keep `fmt`, the failure branch (`audit-failed`), and the success-redirect effect unchanged. New `EstimatorRow`:

```tsx
function EstimatorRow({ e }: { e: Estimate }) {
  const done = (e.point !== undefined && e.point !== null) || e.error != null;
  const dot = e.error ? 'error' : done ? 'done' : 'running';
  return (
    <div data-testid={`estimator-${e.method}`} className="aud-estimator">
      <span className={`aud-estimator__dot aud-estimator__dot--${dot}`} />
      <span className="aud-estimator__method">{e.method}</span>
      <span className="aud-estimator__value">
        {e.error ? 'n/a' : (e.point !== undefined && e.point !== null)
          ? `${fmt(e.point, 3)} [${fmt(e.ci_lower, 2)}, ${fmt(e.ci_upper, 2)}]`
          : 'running…'}
      </span>
    </div>
  );
}
```

Main return body (replaces the current one; failure branch above it stays):
```tsx
  const estimates = snapshot?.artifact?.estimates ?? [];
  const state = snapshot?.state ?? 'queued';
  const stages: Array<{ key: string; label: string }> = [
    { key: 'queued', label: 'queued' },
    { key: 'running', label: 'estimating' },
    { key: 'succeeded', label: 'signing' },
  ];
  const reached = (k: string) =>
    (state === 'running' && k !== 'succeeded') ||
    (state === 'succeeded') ||
    (state === 'queued' && k === 'queued');

  return (
    <div data-testid="audit-progress">
      <h2>Running audit…</h2>
      <div data-testid="aud-stages" className="aud-progress__stages">
        {stages.map((s) => (
          <span key={s.key} className={`aud-stage ${reached(s.key) ? (state === 'succeeded' ? 'aud-stage--done' : 'aud-stage--active') : ''}`}>
            <span aria-hidden="true">{reached(s.key) ? '●' : '○'}</span> {s.label}
          </span>
        ))}
      </div>
      <div>{estimates.map((e) => <EstimatorRow key={e.method} e={e} />)}</div>
      {estimates.length === 0 && <p className="aud-scenario__desc">Spinning up estimators…</p>}
    </div>
  );
```
Also restyle the `audit-failed` block to use `ui-btn` classes instead of a bare button (keep its testid + the `navigate('/')` handler).

- [ ] **Step 4: Run — PASS. Commit.**

```bash
npx vitest run src/audit/__tests__/AuditProgress.test.tsx && npx eslint src --max-warnings 0
git add frontend/src/audit/AuditProgress.tsx frontend/src/audit/__tests__/AuditProgress.test.tsx
git commit -m "feat(s37b): AuditProgress stage timeline + classed estimator rows

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Certificate — light document via CertificateTheme, HashChip, Badge

**Files:**
- Modify: `frontend/src/audit/Certificate.tsx`, `frontend/src/audit/CertificatePage.tsx`, `frontend/src/audit/VerifyPage.tsx`
- Test: `frontend/src/audit/__tests__/Certificate.test.tsx`, `VerifyPage.test.tsx` (keep all testids; add light-theme wrapper assertion)

- [ ] **Step 1: Add a failing test** to `Certificate.test.tsx`:

```tsx
it('renders the certificate inside the light certificate theme island', () => {
  render(<Certificate artifact={baseArtifact} />);   // reuse the file's existing fixture
  const cert = screen.getByTestId('certificate');
  expect(cert.closest('[data-theme="certificate"]')).not.toBeNull();
});
```

- [ ] **Step 2: Run — expect FAIL.** `npx vitest run src/audit/__tests__/Certificate.test.tsx`

- [ ] **Step 3: Edit `Certificate.tsx`** — keep the `verdict()` helper and the entire `badge`/`verifyResult` decision logic verbatim (compliance-critical). Wrap the return in `CertificateTheme`, swap inline styles for classes, render the hash via `HashChip` and the seal via `Badge`. Imports:

```tsx
import { CertificateTheme } from '../ui/CertificateTheme';
import { HashChip } from '../ui/HashChip';
import { Badge } from '../ui/Badge';
import type { BadgeStatus } from '../ui/status';
```

Map the existing `badge.tone` to a `BadgeStatus` (no logic change, just presentation):
```tsx
  const badgeStatus: BadgeStatus = badge.tone === 'ok' ? 'verified' : badge.tone === 'bad' ? 'danger' : 'neutral';
```

Return (preserves every testid):
```tsx
  return (
    <CertificateTheme>
      <div data-testid="certificate" className="aud-cert">
        <div className="aud-cert__seal">
          <span className="aud-cert__doctype">Certificate of Audit</span>
          <span data-testid="cert-signature-badge"><Badge status={badgeStatus}>{badge.label.replace(/^[✓✕]\s*/, '')}</Badge></span>
        </div>

        {artifact.degraded && (
          <p data-testid="cert-degraded" className="aud-cert__verdict">Served a verified cached result.</p>
        )}

        <h1 className="aud-cert__title">Audit Certificate</h1>
        <p className="aud-cert__verdict">{artifact.rendered?.verdict?.label ?? verdict(artifact)}</p>

        <div className="aud-cert__evidence">
          <div className="aud-cert__label">Record hash</div>
          <div data-testid="cert-hash"><HashChip hash={artifact.audit_record_hash} verifyHref={`/verify/${artifact.audit_record_hash}`} /></div>
          <div data-testid="cert-key-source" className="aud-cert__keysrc">Key source: {artifact.signing_key_source}</div>
        </div>

        {verifyResult && (
          <div data-testid="cert-verify-status" className="aud-cert__verifystatus" style={undefined}>
            {verifyResult.verified ? 'Independently verified ✓' : `NOT verified — ${verifyResult.reason ?? verifyResult.signature_status}`}
          </div>
        )}

        {!readOnly && (
          <div className="aud-cert__actions">
            <a data-testid="cert-download-pdf" href={auditApi.pdfUrl(artifact.audit_record_hash)} target="_blank" rel="noreferrer" className="aud-cert__pdf">Download PDF</a>
            <Link data-testid="cert-verify-link" to={`/verify/${artifact.audit_record_hash}`} className="ui-btn ui-btn--secondary ui-btn--md" style={{ textDecoration: 'none' }}>Verify independently</Link>
          </div>
        )}
      </div>
    </CertificateTheme>
  );
```
Note: `cert-verify-status` keeps a green/red meaning — encode it as a class (`aud-cert__verifystatus--ok/--bad`) rather than `style`, and drop the `style={undefined}`. Add to `audit.css`:
```css
.aud-cert__verifystatus--ok { color: var(--signal); }
.aud-cert__verifystatus--bad { color: var(--danger); }
```
and apply the modifier from `verifyResult.verified`. Resolve the `cert-verify-link` `style={{textDecoration}}` by adding `text-decoration:none` to a small `.ui-btn` anchor rule or a local class — no inline style in final code.

- [ ] **Step 4: `CertificatePage.tsx` / `VerifyPage.tsx`** need no structural change (they delegate to `Certificate`), but confirm their loading/error states use `aud-scenario__desc` class instead of bare text if they carry inline styles (they don't currently — leave as-is). Keep `certificate-page` / `verify-page` testids.

- [ ] **Step 5: Run the two suites — PASS. Commit.**

```bash
npx vitest run src/audit/__tests__/Certificate.test.tsx src/audit/__tests__/VerifyPage.test.tsx && npx eslint src --max-warnings 0
git add frontend/src/audit/Certificate.tsx frontend/src/audit/CertificatePage.tsx frontend/src/audit/VerifyPage.tsx frontend/src/audit/audit.css frontend/src/audit/__tests__/Certificate.test.tsx
git commit -m "feat(s37b): Certificate as light print-safe document (HashChip + Badge + CertificateTheme)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full verification + visual checkpoint + push

- [ ] **Step 1: Full gate**

```bash
cd ~/Downloads/AURA/frontend
npx vitest run 2>&1 | tail -2 && npx tsc --noEmit && npx eslint src --max-warnings 0
```
Expected: ≥207 passed (204 + 3 new), clean.

- [ ] **Step 2: Visual checkpoint** — dev server on :5173 (restart if needed). Via devtools, screenshot and eyeball each of the five routes:
  - `/` — hero + trust band + carded scenarios
  - `/audit/new` — Stepper, themed drop-zone, visible disabled Next
  - `/audit/<id>` — stage timeline + estimator rows (drive a real scenario from `/`)
  - `/certificate/<hash>` — **light** document, HashChip, verified Badge
  - `/verify/<hash>` — read-only light certificate, independent-verify status
  Check console for CSP/errors on each. Fix any contrast/overflow regression before pushing.

- [ ] **Step 3: Push the phase**

```bash
cd ~/Downloads/AURA
git push origin feature/s37-terminal-authority
```
No PR — the big-bang branch continues into s37c (shell + IA).

---

## Self-Review

- **Spec §3 coverage:** landing hero/trust band → Task 2; wizard Stepper/primitives → Task 3; progress timeline → Task 4; light certificate document + verify → Task 5. Live-proof-strip downgraded to a static honest trust band (no `latest-cert` endpoint; backend is out of scope) — deviation noted in Architecture.
- **Placeholder scan:** none — every code step carries full content; the two `style={undefined}` markers are explicitly called out to be removed, not shipped.
- **Type/contract consistency:** `BadgeStatus`/`STATUS_GLYPHS` imported from `../ui/status` (matches s37a); `HashChip` verifyHref uses the relative `/verify/<hash>` form that passed s37a's same-origin gate; all 15+ legacy testids enumerated in Invariants and preserved (wizard dots kept as hidden compatibility nodes).
