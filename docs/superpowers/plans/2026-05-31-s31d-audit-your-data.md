# S31d — "Audit Your Own Data" Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `/audit/new` into a guided 3-step Upload → Map → Review wizard that uploads a CSV, maps columns to causal roles, and runs a real audit via `POST /audit`, reusing the S31a progress + certificate views.

**Architecture:** Frontend-only. A `FileReader` + pure CSV parser give an instant client-side preview and column dropdowns; pure `validateMapping` drives live validation; on Run the wizard POSTs `{uploaded_file, treatment, outcome, confounders, instrument?}` to `/counterfactual/audit` → `{job_id}` → navigates to the existing `/audit/{job_id}` flow. The dataset is uploaded via the existing `/upload` endpoint (lands in `data/uploads`, where `/audit` resolves it).

**Tech Stack:** React 19, TypeScript, Vite, Vitest + Testing Library, react-router-dom v7. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-s31d-audit-your-data-design.md`

**Conventions (verified in repo, same as S31a):**
- Tests: `import { render, screen, waitFor } from '@testing-library/react'`, `userEvent` from `@testing-library/user-event`, `describe/expect/it/vi` from `vitest`. Query by `data-testid`.
- Components using router hooks render inside `<MemoryRouter>`; mock navigation via `vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }))`.
- Mock fetch with `vi.stubGlobal('fetch', vi.fn())` in `beforeEach`, `vi.unstubAllGlobals()` in `afterEach`.
- API base: `import { API_BASE_URL } from '../services/api'`; `const CF = \`${API_BASE_URL}/counterfactual\`` is already defined in `auditApi.ts`.
- Inline CSS-var styles (`var(--space-*)`, `var(--accent)`, etc.) — no separate CSS files.
- Pre-push (from `frontend/`): `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run`.
- Commit co-author: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/audit/types.ts` | **Modify** — add `ColumnType`, `ColumnMapping`, `DataAuditRequest`. |
| `frontend/src/audit/auditApi.ts` | **Modify** — add `uploadDataset(file)` + `runDataAudit(req)`; remove now-unused `submitCustomAudit`. |
| `frontend/src/audit/csv.ts` | **Create** — pure `parseCsvHeadAndRows(text, maxRows)`. |
| `frontend/src/audit/validateMapping.ts` | **Create** — pure `validateMapping(mapping, columns)`. |
| `frontend/src/audit/useCsvPreview.ts` | **Create** — `File` → `FileReader` + `csv.ts` reactive hook. |
| `frontend/src/audit/wizard/UploadStep.tsx` | **Create** — file input + instant preview table. |
| `frontend/src/audit/wizard/MapStep.tsx` | **Create** — role dropdowns + inline errors. |
| `frontend/src/audit/wizard/ReviewStep.tsx` | **Create** — mapping summary + Run. |
| `frontend/src/audit/AuditWizard.tsx` | **Rewrite** — 3-step orchestrator. |
| `frontend/src/audit/__tests__/*` | tests per unit; **replace** the existing `AuditWizard.test.tsx`. |

---

## Task 1: Types + API client methods

**Files:**
- Modify: `frontend/src/audit/types.ts`
- Modify: `frontend/src/audit/auditApi.ts`
- Modify: `frontend/src/audit/__tests__/auditApi.test.ts`

- [ ] **Step 1: Add types**

Append to `frontend/src/audit/types.ts` (note: `ColumnType` is owned by `csv.ts` in Task 2 — do **not** redeclare it here):

```ts
export interface ColumnMapping {
  treatment: string;
  outcome: string;
  confounders: string[];
  instrument?: string;
}

export interface DataAuditRequest {
  uploaded_file: string;
  treatment: string;
  outcome: string;
  confounders: string[];
  instrument?: string;
}
```

- [ ] **Step 2: Write failing tests for the new auditApi methods**

In `frontend/src/audit/__tests__/auditApi.test.ts`, **remove** the existing `submitCustomAudit` test case, and add (inside the existing `describe('auditApi', …)`):

```ts
  it('uploadDataset POSTs multipart to /upload and returns the filename', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ upload_id: 'u1', filename: 'data.csv', bytes: 10, status: 'success' }));
    const file = new File(['a,b\n1,2\n'], 'data.csv', { type: 'text/csv' });
    const out = await auditApi.uploadDataset(file);
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/upload`, expect.objectContaining({ method: 'POST' }));
    expect(out.filename).toBe('data.csv');
  });

  it('runDataAudit POSTs the AuditRequest to /counterfactual/audit', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ job_id: 'audit_1' }));
    const out = await auditApi.runDataAudit({ uploaded_file: 'data.csv', treatment: 't', outcome: 'y', confounders: ['c1'] });
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/audit`, expect.objectContaining({ method: 'POST' }));
    expect(out.job_id).toBe('audit_1');
  });
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npx vitest run src/audit/__tests__/auditApi.test.ts`
Expected: FAIL — `auditApi.uploadDataset`/`runDataAudit` are not functions.

- [ ] **Step 4: Implement the methods; remove submitCustomAudit**

In `frontend/src/audit/auditApi.ts`: add `import type { ... DataAuditRequest }` to the existing type import line, **delete** the `submitCustomAudit` method, and add these two methods to the `auditApi` object:

```ts
  async uploadDataset(file: File): Promise<{ filename: string }> {
    const form = new FormData();
    form.append('file', file);
    const resp = await fetch(`${API_BASE_URL}/upload`, { method: 'POST', body: form });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    const body = (await resp.json()) as { filename: string };
    return { filename: body.filename };
  },

  async runDataAudit(req: DataAuditRequest): Promise<{ job_id: string }> {
    const resp = await fetch(`${CF}/audit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json() as Promise<{ job_id: string }>;
  },
```

The type import line becomes:
```ts
import type { Scenario, JobSnapshot, DemoSubmitResult, VerifyResult, Artifact, DataAuditRequest } from './types';
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run src/audit/__tests__/auditApi.test.ts`
Expected: PASS (all cases, including the two new ones; submitCustomAudit case removed).

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/audit/types.ts src/audit/auditApi.ts src/audit/__tests__/auditApi.test.ts
git commit -m "feat(s31d): auditApi uploadDataset + runDataAudit; drop submitCustomAudit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Pure CSV parser

**Files:**
- Create: `frontend/src/audit/csv.ts`
- Test: `frontend/src/audit/__tests__/csv.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/audit/__tests__/csv.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { parseCsvHeadAndRows } from '../csv';

describe('parseCsvHeadAndRows', () => {
  it('parses a simple CSV into columns + rows', () => {
    const out = parseCsvHeadAndRows('a,b,c\n1,2,3\n4,5,6\n');
    expect(out.columns).toEqual(['a', 'b', 'c']);
    expect(out.rows).toEqual([['1', '2', '3'], ['4', '5', '6']]);
  });

  it('handles quoted fields containing commas', () => {
    const out = parseCsvHeadAndRows('name,note\n"Doe, John","says ""hi"""\n');
    expect(out.columns).toEqual(['name', 'note']);
    expect(out.rows[0]).toEqual(['Doe, John', 'says "hi"']);
  });

  it('handles CRLF line endings', () => {
    const out = parseCsvHeadAndRows('a,b\r\n1,2\r\n');
    expect(out.columns).toEqual(['a', 'b']);
    expect(out.rows).toEqual([['1', '2']]);
  });

  it('infers number vs string column types from sampled rows', () => {
    const out = parseCsvHeadAndRows('age,name\n30,alice\n40,bob\n');
    expect(out.types).toEqual({ age: 'number', name: 'string' });
  });

  it('returns empty columns for empty/whitespace input without throwing', () => {
    expect(parseCsvHeadAndRows('').columns).toEqual([]);
    expect(parseCsvHeadAndRows('   \n  ').columns).toEqual([]);
  });

  it('caps rows at maxRows', () => {
    const text = 'a\n' + Array.from({ length: 50 }, (_, i) => String(i)).join('\n');
    expect(parseCsvHeadAndRows(text, 10).rows).toHaveLength(10);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/csv.test.ts`
Expected: FAIL — `Cannot find module '../csv'`.

- [ ] **Step 3: Implement the parser**

Create `frontend/src/audit/csv.ts`:

```ts
export type ColumnType = 'number' | 'string';

export interface CsvHead {
  columns: string[];
  rows: string[][];
  types: Record<string, ColumnType>;
}

// Parse a single CSV line: double-quoted fields may contain commas and escaped
// quotes (""). Fields are trimmed. (Embedded newlines inside quotes are not
// supported — this feeds the preview UI only; the backend parses authoritatively.)
function parseLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else { inQuotes = false; }
      } else { cur += ch; }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      out.push(cur); cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

export function parseCsvHeadAndRows(text: string, maxRows = 20): CsvHead {
  const lines = text.split(/\r\n|\r|\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { columns: [], rows: [], types: {} };
  const columns = parseLine(lines[0]);
  const rows = lines.slice(1, 1 + maxRows).map(parseLine);
  const types: Record<string, ColumnType> = {};
  columns.forEach((col, idx) => {
    const samples = rows.map((r) => r[idx]).filter((v) => v !== undefined && v !== '');
    const allNum = samples.length > 0 && samples.every((v) => !Number.isNaN(Number(v)));
    types[col] = allNum ? 'number' : 'string';
  });
  return { columns, rows, types };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/csv.test.ts`
Expected: PASS (all 6 cases).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/audit/csv.ts src/audit/__tests__/csv.test.ts
git commit -m "feat(s31d): pure client-side CSV head/preview parser

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Pure mapping validator

**Files:**
- Create: `frontend/src/audit/validateMapping.ts`
- Test: `frontend/src/audit/__tests__/validateMapping.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/audit/__tests__/validateMapping.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { validateMapping } from '../validateMapping';
import type { ColumnMapping } from '../types';

const COLS = ['t', 'y', 'c1', 'c2', 'iv'];
const base: ColumnMapping = { treatment: 't', outcome: 'y', confounders: ['c1'] };

describe('validateMapping', () => {
  it('accepts a complete valid mapping', () => {
    expect(validateMapping(base, COLS).valid).toBe(true);
  });

  it('requires treatment, outcome, and at least one confounder', () => {
    const r = validateMapping({ treatment: '', outcome: '', confounders: [] }, COLS);
    expect(r.valid).toBe(false);
    expect(r.errors.treatment).toBeTruthy();
    expect(r.errors.outcome).toBeTruthy();
    expect(r.errors.confounders).toBeTruthy();
  });

  it('rejects treatment === outcome', () => {
    expect(validateMapping({ ...base, outcome: 't' }, COLS).errors.outcome).toBeTruthy();
  });

  it('rejects a confounder that is the treatment or outcome', () => {
    expect(validateMapping({ ...base, confounders: ['t'] }, COLS).errors.confounders).toBeTruthy();
    expect(validateMapping({ ...base, confounders: ['y'] }, COLS).errors.confounders).toBeTruthy();
  });

  it('rejects an instrument that collides with another role', () => {
    expect(validateMapping({ ...base, instrument: 't' }, COLS).errors.instrument).toBeTruthy();
    expect(validateMapping({ ...base, instrument: 'c1' }, COLS).errors.instrument).toBeTruthy();
  });

  it('accepts a valid distinct instrument', () => {
    expect(validateMapping({ ...base, instrument: 'iv' }, COLS).valid).toBe(true);
  });

  it('rejects a column not present in the parsed header (stale mapping)', () => {
    expect(validateMapping({ ...base, treatment: 'gone' }, COLS).errors.treatment).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/validateMapping.test.ts`
Expected: FAIL — `Cannot find module '../validateMapping'`.

- [ ] **Step 3: Implement the validator**

Create `frontend/src/audit/validateMapping.ts`:

```ts
import type { ColumnMapping } from './types';

export interface MappingErrors {
  treatment?: string;
  outcome?: string;
  confounders?: string;
  instrument?: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: MappingErrors;
}

export function validateMapping(m: ColumnMapping, columns: string[]): ValidationResult {
  const errors: MappingErrors = {};
  const has = (c: string) => columns.includes(c);

  if (!m.treatment) errors.treatment = 'Select a treatment column.';
  else if (!has(m.treatment)) errors.treatment = 'Column not found in file.';

  if (!m.outcome) errors.outcome = 'Select an outcome column.';
  else if (!has(m.outcome)) errors.outcome = 'Column not found in file.';
  else if (m.treatment && m.outcome === m.treatment) errors.outcome = 'Outcome must differ from treatment.';

  if (!m.confounders || m.confounders.length === 0) errors.confounders = 'Pick at least one confounder.';
  else if (m.confounders.some((c) => !has(c))) errors.confounders = 'A confounder is not in the file.';
  else if (m.treatment && m.confounders.includes(m.treatment)) errors.confounders = 'Confounders cannot include the treatment.';
  else if (m.outcome && m.confounders.includes(m.outcome)) errors.confounders = 'Confounders cannot include the outcome.';

  if (m.instrument) {
    if (!has(m.instrument)) errors.instrument = 'Column not found in file.';
    else if (m.instrument === m.treatment || m.instrument === m.outcome) errors.instrument = 'Instrument must be a distinct column.';
    else if (m.confounders?.includes(m.instrument)) errors.instrument = 'Instrument cannot also be a confounder.';
  }

  return { valid: Object.keys(errors).length === 0, errors };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/validateMapping.test.ts`
Expected: PASS (all 7 cases).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/audit/validateMapping.ts src/audit/__tests__/validateMapping.test.ts
git commit -m "feat(s31d): pure column-mapping validator

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: useCsvPreview hook

**Files:**
- Create: `frontend/src/audit/useCsvPreview.ts`
- Test: `frontend/src/audit/__tests__/useCsvPreview.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/audit/__tests__/useCsvPreview.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useCsvPreview } from '../useCsvPreview';

describe('useCsvPreview', () => {
  it('parses columns/rows/types from a selected File', async () => {
    const file = new File(['age,name\n30,alice\n40,bob\n'], 'd.csv', { type: 'text/csv' });
    const { result } = renderHook(() => useCsvPreview(file));
    await waitFor(() => expect(result.current.columns).toEqual(['age', 'name']));
    expect(result.current.types).toEqual({ age: 'number', name: 'string' });
    expect(result.current.previewRows[0]).toEqual(['30', 'alice']);
  });

  it('resets to empty when file is null', () => {
    const { result } = renderHook(() => useCsvPreview(null));
    expect(result.current.columns).toEqual([]);
    expect(result.current.error).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/useCsvPreview.test.ts`
Expected: FAIL — `Cannot find module '../useCsvPreview'`.

- [ ] **Step 3: Implement the hook**

Create `frontend/src/audit/useCsvPreview.ts`:

```ts
import { useEffect, useState } from 'react';
import { parseCsvHeadAndRows, type ColumnType } from './csv';

export interface CsvPreview {
  columns: string[];
  previewRows: string[][];
  types: Record<string, ColumnType>;
  error: string | null;
  loading: boolean;
}

const EMPTY: CsvPreview = { columns: [], previewRows: [], types: {}, error: null, loading: false };

export function useCsvPreview(file: File | null): CsvPreview {
  const [state, setState] = useState<CsvPreview>(EMPTY);

  useEffect(() => {
    if (!file) { setState(EMPTY); return; }
    let cancelled = false;
    setState({ ...EMPTY, loading: true });
    const reader = new FileReader();
    reader.onload = () => {
      if (cancelled) return;
      try {
        const head = parseCsvHeadAndRows(String(reader.result ?? ''));
        setState({ columns: head.columns, previewRows: head.rows, types: head.types, error: null, loading: false });
      } catch (e) {
        setState({ ...EMPTY, error: e instanceof Error ? e.message : String(e) });
      }
    };
    reader.onerror = () => { if (!cancelled) setState({ ...EMPTY, error: 'Could not read file.' }); };
    reader.readAsText(file);
    return () => { cancelled = true; };
  }, [file]);

  return state;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/audit/__tests__/useCsvPreview.test.ts`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/audit/useCsvPreview.ts src/audit/__tests__/useCsvPreview.test.ts
git commit -m "feat(s31d): useCsvPreview hook (FileReader + csv parse)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Wizard step components

**Files:**
- Create: `frontend/src/audit/wizard/UploadStep.tsx`
- Create: `frontend/src/audit/wizard/MapStep.tsx`
- Create: `frontend/src/audit/wizard/ReviewStep.tsx`

These are presentational, prop-driven, and exercised through the wizard integration test in Task 6 (no separate unit tests — they hold no logic of their own; logic lives in `csv.ts`/`validateMapping.ts`/the orchestrator).

- [ ] **Step 1: Create UploadStep**

Create `frontend/src/audit/wizard/UploadStep.tsx`:

```tsx
import type { ColumnType } from '../csv';

export function UploadStep({ file, columns, previewRows, types, uploading, error, onPick }: {
  file: File | null;
  columns: string[];
  previewRows: string[][];
  types: Record<string, ColumnType>;
  uploading: boolean;
  error: string | null;
  onPick: (file: File) => void;
}) {
  return (
    <div data-testid="wizard-step-upload">
      <h3>1 · Upload your dataset</h3>
      <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-sm)' }}>A CSV with one row per decision. We parse it in your browser instantly; nothing is shared until you run the audit.</p>
      <input
        data-testid="wizard-file-input"
        type="file"
        accept=".csv,text/csv"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onPick(f); }}
        style={{ display: 'block', margin: 'var(--space-4) 0' }}
      />
      {uploading && <p data-testid="wizard-uploading" style={{ fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>Uploading {file?.name}…</p>}
      {error && <p data-testid="wizard-upload-error" style={{ color: 'var(--red)' }}>{error}</p>}
      {columns.length > 0 && (
        <div data-testid="wizard-preview" style={{ overflowX: 'auto', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 'var(--font-sm)', width: '100%' }}>
            <thead>
              <tr>{columns.map((c) => (
                <th key={c} style={{ textAlign: 'left', padding: 'var(--space-2)', borderBottom: '1px solid var(--border-default)' }}>
                  {c} <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>({types[c]})</span>
                </th>
              ))}</tr>
            </thead>
            <tbody>
              {previewRows.slice(0, 5).map((r, i) => (
                <tr key={i}>{columns.map((_, j) => <td key={j} style={{ padding: 'var(--space-2)', borderBottom: '1px solid var(--border-default)' }}>{r[j]}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create MapStep**

Create `frontend/src/audit/wizard/MapStep.tsx`:

```tsx
import type { ColumnMapping } from '../types';
import type { MappingErrors } from '../validateMapping';

export function MapStep({ columns, mapping, errors, onChange }: {
  columns: string[];
  mapping: ColumnMapping;
  errors: MappingErrors;
  onChange: (next: ColumnMapping) => void;
}) {
  const set = (patch: Partial<ColumnMapping>) => onChange({ ...mapping, ...patch });
  const fieldStyle = { width: '100%', padding: 'var(--space-3)', background: 'var(--bg-base)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)' };

  const toggleConfounder = (col: string) => {
    const next = mapping.confounders.includes(col)
      ? mapping.confounders.filter((c) => c !== col)
      : [...mapping.confounders, col];
    set({ confounders: next });
  };

  return (
    <div data-testid="wizard-step-map">
      <h3>2 · Map columns to causal roles</h3>

      <label style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
        <span style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>Treatment</span>
        <select data-testid="map-treatment" value={mapping.treatment} onChange={(e) => set({ treatment: e.target.value })} style={fieldStyle}>
          <option value="">— select —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.treatment && <span data-testid="err-treatment" style={{ color: 'var(--red)', fontSize: 'var(--font-xs)' }}>{errors.treatment}</span>}
      </label>

      <label style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
        <span style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>Outcome</span>
        <select data-testid="map-outcome" value={mapping.outcome} onChange={(e) => set({ outcome: e.target.value })} style={fieldStyle}>
          <option value="">— select —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.outcome && <span data-testid="err-outcome" style={{ color: 'var(--red)', fontSize: 'var(--font-xs)' }}>{errors.outcome}</span>}
      </label>

      <div style={{ marginBottom: 'var(--space-4)' }}>
        <span style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>Confounders</span>
        <div data-testid="map-confounders" style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginTop: 'var(--space-1)' }}>
          {columns.map((c) => (
            <button key={c} type="button" data-testid={`confounder-${c}`} onClick={() => toggleConfounder(c)}
              style={{ padding: 'var(--space-1) var(--space-3)', borderRadius: 'var(--radius-full)', cursor: 'pointer',
                border: `1px solid ${mapping.confounders.includes(c) ? 'var(--accent)' : 'var(--border-default)'}`,
                background: mapping.confounders.includes(c) ? 'var(--accent)' : 'transparent',
                color: mapping.confounders.includes(c) ? '#fff' : 'var(--text-secondary)' }}>
              {c}
            </button>
          ))}
        </div>
        {errors.confounders && <span data-testid="err-confounders" style={{ color: 'var(--red)', fontSize: 'var(--font-xs)' }}>{errors.confounders}</span>}
      </div>

      <label style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
        <span style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>Instrument (optional — enables IV)</span>
        <select data-testid="map-instrument" value={mapping.instrument ?? ''} onChange={(e) => set({ instrument: e.target.value || undefined })} style={fieldStyle}>
          <option value="">— none —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.instrument && <span data-testid="err-instrument" style={{ color: 'var(--red)', fontSize: 'var(--font-xs)' }}>{errors.instrument}</span>}
      </label>
    </div>
  );
}
```

- [ ] **Step 3: Create ReviewStep**

Create `frontend/src/audit/wizard/ReviewStep.tsx`:

```tsx
import type { ColumnMapping } from '../types';

export function ReviewStep({ filename, mapping }: { filename: string | null; mapping: ColumnMapping }) {
  const row = (label: string, value: string) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: 'var(--space-2) 0', borderBottom: '1px solid var(--border-default)' }}>
      <span style={{ color: 'var(--text-tertiary)' }}>{label}</span>
      <span style={{ fontFamily: 'monospace' }}>{value}</span>
    </div>
  );
  return (
    <div data-testid="wizard-step-review">
      <h3>3 · Review &amp; run</h3>
      {row('Dataset', filename ?? '—')}
      {row('Treatment', mapping.treatment)}
      {row('Outcome', mapping.outcome)}
      {row('Confounders', mapping.confounders.join(', '))}
      {row('Instrument', mapping.instrument ?? 'none')}
    </div>
  );
}
```

- [ ] **Step 4: Type-check (no test yet — wired in Task 6)**

Run: `npx tsc --noEmit`
Expected: clean (exit 0). (These components are unused until Task 6; tsc still type-checks them.)

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/audit/wizard/
git commit -m "feat(s31d): wizard step components (upload/map/review)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Wizard orchestrator + integration test

**Files:**
- Rewrite: `frontend/src/audit/AuditWizard.tsx`
- Replace: `frontend/src/audit/__tests__/AuditWizard.test.tsx`

- [ ] **Step 1: Replace the wizard test with the new integration test**

Overwrite `frontend/src/audit/__tests__/AuditWizard.test.tsx` entirely:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

import { AuditWizard } from '../AuditWizard';
import { auditApi } from '../auditApi';

function csvFile() {
  return new File(['protected_class,approved,income,officer\n1,0,50000,A\n0,1,60000,B\n'], 'loans.csv', { type: 'text/csv' });
}

describe('AuditWizard (audit your own data)', () => {
  afterEach(() => { vi.restoreAllMocks(); navigate.mockClear(); });

  it('uploads a CSV, maps columns, and runs the audit', async () => {
    vi.spyOn(auditApi, 'uploadDataset').mockResolvedValue({ filename: 'loans.csv' });
    const run = vi.spyOn(auditApi, 'runDataAudit').mockResolvedValue({ job_id: 'audit_42' });
    const user = userEvent.setup();
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);

    // Step 1: pick a file → columns parse + upload fires
    await user.upload(screen.getByTestId('wizard-file-input'), csvFile());
    await waitFor(() => expect(screen.getByTestId('wizard-preview')).toBeInTheDocument());
    await waitFor(() => expect(auditApi.uploadDataset).toHaveBeenCalled());
    // Wait for upload to resolve (filename set) so Next is enabled.
    await waitFor(() => expect(screen.getByTestId('wizard-next')).toBeEnabled());
    await user.click(screen.getByTestId('wizard-next'));

    // Step 2: map roles
    await user.selectOptions(screen.getByTestId('map-treatment'), 'protected_class');
    await user.selectOptions(screen.getByTestId('map-outcome'), 'approved');
    await user.click(screen.getByTestId('confounder-income'));
    await user.click(screen.getByTestId('wizard-next'));

    // Step 3: run
    await user.click(screen.getByTestId('wizard-run'));
    await waitFor(() => expect(run).toHaveBeenCalledWith(expect.objectContaining({
      uploaded_file: 'loans.csv', treatment: 'protected_class', outcome: 'approved', confounders: ['income'],
    })));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/audit/audit_42'));
  });

  it('blocks advancing past mapping until the mapping is valid', async () => {
    vi.spyOn(auditApi, 'uploadDataset').mockResolvedValue({ filename: 'loans.csv' });
    const user = userEvent.setup();
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);
    await user.upload(screen.getByTestId('wizard-file-input'), csvFile());
    await waitFor(() => screen.getByTestId('wizard-preview'));
    await user.click(screen.getByTestId('wizard-next')); // to map step
    // No roles chosen → Next disabled, validation error shown
    expect(screen.getByTestId('wizard-next')).toBeDisabled();
    expect(screen.getByTestId('err-treatment')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/audit/__tests__/AuditWizard.test.tsx`
Expected: FAIL — the old wizard has no `wizard-file-input`/step structure.

- [ ] **Step 3: Rewrite the orchestrator**

Overwrite `frontend/src/audit/AuditWizard.tsx` entirely:

```tsx
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auditApi } from './auditApi';
import { useCsvPreview } from './useCsvPreview';
import { validateMapping } from './validateMapping';
import type { ColumnMapping } from './types';
import { UploadStep } from './wizard/UploadStep';
import { MapStep } from './wizard/MapStep';
import { ReviewStep } from './wizard/ReviewStep';

const EMPTY_MAPPING: ColumnMapping = { treatment: '', outcome: '', confounders: [] };

export function AuditWizard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [mapping, setMapping] = useState<ColumnMapping>(EMPTY_MAPPING);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const preview = useCsvPreview(file);
  const validation = useMemo(() => validateMapping(mapping, preview.columns), [mapping, preview.columns]);

  const pickFile = async (f: File) => {
    setFile(f);
    setMapping(EMPTY_MAPPING);
    setUploadError(null);
    setUploading(true);
    try {
      const { filename: name } = await auditApi.uploadDataset(f);
      setFilename(name);
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const run = async () => {
    if (!filename) return;
    setRunning(true);
    setRunError(null);
    try {
      const { job_id } = await auditApi.runDataAudit({
        uploaded_file: filename,
        treatment: mapping.treatment,
        outcome: mapping.outcome,
        confounders: mapping.confounders,
        instrument: mapping.instrument,
      });
      navigate(`/audit/${job_id}`);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
      setRunning(false);
    }
  };

  // Step 0 (upload) advances once columns parsed + upload finished without error.
  // Step 1 (map) advances only when the mapping is valid.
  const canNext = step === 0
    ? preview.columns.length > 0 && !uploading && !uploadError && filename !== null
    : step === 1
      ? validation.valid
      : false;

  const btn = (testid: string, label: string, enabled: boolean, onClick: () => void) => (
    <button data-testid={testid} disabled={!enabled} onClick={onClick}
      style={{ padding: 'var(--space-3) var(--space-6)', background: enabled ? 'var(--accent)' : 'var(--border-default)', color: '#fff', border: 'none', borderRadius: 'var(--radius-md)', cursor: enabled ? 'pointer' : 'not-allowed' }}>
      {label}
    </button>
  );

  return (
    <div data-testid="audit-wizard" style={{ maxWidth: 640, margin: '0 auto' }}>
      <h2>Audit your own data</h2>
      <div style={{ display: 'flex', gap: 'var(--space-2)', margin: 'var(--space-3) 0 var(--space-6)' }}>
        {['Upload', 'Map', 'Review'].map((label, i) => (
          <span key={label} data-testid={`wizard-dot-${i}`} style={{ fontSize: 'var(--font-xs)', textTransform: 'uppercase', letterSpacing: '0.06em',
            color: i === step ? 'var(--accent)' : i < step ? 'var(--green)' : 'var(--text-tertiary)' }}>
            {i + 1}. {label}
          </span>
        ))}
      </div>

      {step === 0 && <UploadStep file={file} columns={preview.columns} previewRows={preview.previewRows} types={preview.types} uploading={uploading} error={uploadError} onPick={pickFile} />}
      {step === 1 && <MapStep columns={preview.columns} mapping={mapping} errors={validation.errors} onChange={setMapping} />}
      {step === 2 && <ReviewStep filename={filename} mapping={mapping} />}

      {runError && <p style={{ color: 'var(--red)' }}>{runError}</p>}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 'var(--space-6)' }}>
        {step > 0
          ? btn('wizard-back', 'Back', true, () => setStep((s) => s - 1))
          : <span />}
        {step < 2
          ? btn('wizard-next', 'Next', canNext, () => setStep((s) => s + 1))
          : btn('wizard-run', running ? 'Running…' : 'Run audit', !running && validation.valid && filename !== null, run)}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the wizard test to verify it passes**

Run: `npx vitest run src/audit/__tests__/AuditWizard.test.tsx`
Expected: PASS (both cases).

- [ ] **Step 5: Full pre-push gate**

Run: `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run`
Expected: all green. (No other file referenced `submitCustomAudit` — the only consumer was the old wizard, now rewritten. If eslint flags an unused import anywhere, remove it.)

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/audit/AuditWizard.tsx src/audit/__tests__/AuditWizard.test.tsx
git commit -m "feat(s31d): wizard orchestrator — upload, map, run /audit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] From `frontend/`: `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run` — all green.
- [ ] Use `superpowers:verification-before-completion` before claiming done.
- [ ] Push branch `feature/s31d-audit-your-data`; open PR titled `Sprint S31d: Audit Your Own Data wizard`; confirm CI (incl. CodeQL) green before merge.

## Spec coverage check

- Upload-in-wizard CSV + lands where `/audit` resolves → Task 1 (`uploadDataset`), Task 5/6 (UploadStep + orchestrator) ✓
- Instant client-side parse + preview + types → Task 2 (`csv.ts`), Task 4 (`useCsvPreview`), Task 5 (UploadStep preview) ✓
- Stepped Upload → Map → Review wizard → Task 5/6 ✓
- Column dropdowns from parsed header → Task 5 (MapStep) ✓
- Live validation rules + Next/Run gating → Task 3 (`validateMapping`), Task 6 (orchestrator gating) ✓
- Instrument (optional, enables IV) → Task 1 (type), Task 5 (MapStep), Task 6 ✓
- Submit to `POST /counterfactual/audit` → `{job_id}` → reuse `/audit/{job_id}` → Task 1 (`runDataAudit`), Task 6 (navigate) ✓
- All Tier A tests on existing FE lane → every task ✓
- No backend changes / no new deps / no websocket → honored throughout ✓
