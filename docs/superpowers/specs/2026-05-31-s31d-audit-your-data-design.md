# S31d — "Audit Your Own Data" Wizard (Frontend) — Design Spec

**Date:** 2026-05-31
**Track:** S31d (YC demo — frontend follow-up) · branch `feature/s31d-audit-your-data`
**Owner:** Rohith
**Builds on:** S31a (front door + progress + certificate, PR #40) and Mounith's
`#42` backend `POST /audit` ("audit your own uploaded data — auto-DAG from column
mapping"). Frontend-only; no backend changes.

---

## 1. Goal

Upgrade the existing custom-audit wizard (`/audit/new`) from a free-text form
that posts to `/jobs` with **no dataset** into a real, guided **upload → map →
audit** flow against `POST /audit`. A user brings their own CSV, maps columns to
causal roles, and gets the same signed certificate the demo scenarios produce.

It must **feel real-time**: the CSV is parsed and previewed the instant a file is
chosen, role mapping is validated live, and the run streams progress (reusing the
S31a polling view). Collaborative/websocket "live" is explicitly **out of scope**
(see §8) — it needs backend WS infra that doesn't exist and contradicts S31b's
polling design.

## 2. What already exists (build on, don't rebuild)

| Capability | Where | Use for S31d |
|---|---|---|
| `POST /counterfactual/audit` → `{ job_id }` (async worker, auto-DAG from column mapping, CSV hygiene, path-traversal guards) | `counterfactual_service/main.py` (`run_audit`), gateway `routers/counterfactual.py` | Submit target. Reuse. |
| File upload → `data/uploads/<filename>` (where `/audit`'s `_find_upload` resolves by name) | gateway `routers/files.py` `POST /upload`; frontend `api.uploadFile()` | Upload step. Reuse. |
| Live progress (`/audit/:jobId`, polls `/jobs/:id`) + formal certificate (`/certificate/:hash`) | `audit/AuditProgress.tsx`, `audit/Certificate.tsx` (S31a) | Reuse unchanged — `/audit` returns the same `{job_id}` shape. |
| Existing `AuditWizard` stub at `/audit/new` (free-text → `/jobs`) | `audit/AuditWizard.tsx` | **Replace** its body. |

## 3. Architecture & flow

`/audit/new` becomes a stateful 3-step wizard. After "Run" it re-converges on the
S31a `/audit/{job_id}` → progress → certificate path (reused, untouched).

```
Step 1 · Upload
  drag/drop or pick a CSV
   → on select: FileReader reads the file, csv.ts parses header + first ~20 rows
     → INSTANT preview table + inferred column types (no network wait)
   → in parallel: auditApi.uploadDataset(file) → { filename }  (lands in data/uploads)
Step 2 · Map columns
  dropdowns from parsed columns: Treatment(1) · Outcome(1) · Confounders(multi) ·
  Instrument(optional, enables IV)
   → validateMapping() runs live; inline errors; Next disabled until valid
Step 3 · Review & run
  mapping summary → Run
   → auditApi.runDataAudit({ uploaded_file, treatment, outcome, confounders, instrument? })
     → POST /counterfactual/audit → { job_id }
   → navigate(/audit/{job_id})  → existing progress (polling) → certificate
```

Key decisions:
- **Instant preview is decoupled from the upload network call.** `FileReader` +
  `csv.ts` populate the dropdowns/preview the moment the file is chosen, even
  while `uploadDataset` is still streaming. The retained `filename` from the
  upload response is what gets submitted.
- **Client-side parsing only feeds the UI** (dropdowns + preview). The backend
  (`#42`) does authoritative CSV hygiene + DAG construction, so a parsing edge
  case can never affect correctness — only the preview display.
- **No new dependency** — a minimal hand-rolled CSV parser (quoted fields,
  commas, CRLF) is sufficient for header + a small preview sample.

## 4. Components

All under `frontend/src/audit/`, each file focused:

| File | Responsibility | Depends on |
|---|---|---|
| `audit/csv.ts` | **Pure** `parseCsvHeadAndRows(text, maxRows) → { columns, rows, types }`; infers `number`\|`string` per column from sampled rows | — |
| `audit/useCsvPreview.ts` | Hook: `File` → `FileReader` + `csv.ts` → `{ columns, previewRows, types, error }` reactively | `csv.ts` |
| `audit/validateMapping.ts` | **Pure** `validateMapping(mapping, columns) → { valid, errors }` — single source of truth for §5 rules | — |
| `audit/wizard/UploadStep.tsx` | Drag/drop + picker; instant preview table + types; triggers `uploadDataset` | `useCsvPreview`, `auditApi` |
| `audit/wizard/MapStep.tsx` | Role dropdowns from parsed columns + inline validation errors | `validateMapping` |
| `audit/wizard/ReviewStep.tsx` | Mapping summary + Run button | — |
| `audit/AuditWizard.tsx` | **Rewritten** orchestrator: step index, mapping + filename + columns state, Run→`runDataAudit`→navigate | all above |
| `audit/auditApi.ts` | Add `uploadDataset(file) → { filename }` (wraps `/upload`) and `runDataAudit(req) → { job_id }` (POST `${CF}/audit`) | `services/api` |
| `audit/types.ts` | Add `DataAuditRequest { uploaded_file, treatment, outcome, confounders, instrument? }`, `ColumnMapping`, `ColumnType` | — |

Rationale: `csv.ts` and `validateMapping.ts` are pure (fully unit-testable
without React/DOM); the step files are thin renderers; `AuditWizard.tsx` shrinks
to orchestration, avoiding a single giant wizard file.

## 5. Validation rules (`validateMapping`, live)

- `treatment` required; `outcome` required; **≥1 confounder**.
- `treatment ≠ outcome`.
- `confounders` must not contain `treatment` or `outcome`.
- `instrument` (optional) — if set, must differ from `treatment`/`outcome` and not
  appear in `confounders`.
- Every referenced column must exist in the parsed header (guards a stale mapping
  after the user re-uploads a different file).

Returns `{ valid: boolean, errors: Partial<Record<role, string>> }` for inline
display and Next/Run gating.

## 6. Contract consumed (existing backend)

```
POST /counterfactual/audit
  body: { uploaded_file: string, treatment: string, outcome: string,
          confounders: string[], instrument?: string }
  → { job_id: string }

POST /upload  (multipart)  → { upload_id, filename, bytes, status }
                              # `filename` is what /audit's _find_upload resolves

GET /counterfactual/jobs/{job_id}            (reused from S31a — progress)
GET /counterfactual/artifacts/{hash}/...     (reused from S31a — certificate/verify)
```

`uploaded_file` is the bare `filename` returned by `/upload`. The backend rejects
path traversal at both the HTTP boundary and the worker (`#42`); the frontend
additionally only ever sends the server-returned filename.

## 7. Testing (all Tier A — fetch/File mocked)

Vitest + Testing Library, existing Frontend CI lane, no new infra.

- `csv.test.ts` — simple CSV; quoted fields with commas; CRLF; numeric-vs-string
  type inference; empty/whitespace input → empty columns (no throw).
- `useCsvPreview.test.ts` — `File`/`Blob` → `{columns, previewRows, types}`;
  unreadable input → `error` set, no crash.
- `validateMapping.test.ts` — each rule in §5: missing required, `treatment==
  outcome`, confounder collision, instrument collision, stale-column reference,
  all-valid.
- `auditApi.test.ts` — add `uploadDataset` (POST `/upload` → `filename`) and
  `runDataAudit` (POST `/counterfactual/audit` → `{job_id}`); error mapping on
  non-OK.
- `AuditWizard.test.tsx` — integration: select a mock CSV `File` → dropdowns
  populate → live validation blocks Next while invalid → Run calls `runDataAudit`
  with the assembled `DataAuditRequest` and navigates to `/audit/{job_id}`.

Pre-push: `npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run`
(from `frontend/`).

## 8. Non-goals (YAGNI)

- **No collaborative/websocket/multi-viewer live updates** — needs backend WS
  infra that doesn't exist and contradicts S31b's polling design. Deferred to a
  separate sprint. "Real-time" here = instant client-side parse/preview + live
  validation + the existing poll-driven progress view.
- No backend changes — consume `POST /audit` and `/upload` as-is.
- No file-picker of previously-uploaded files (upload-in-wizard only, per the
  data-source decision).
- No data editing/cleaning UI — the backend does CSV hygiene.
- No new CSV-parsing dependency — hand-rolled minimal parser for header + preview.

## 9. Build order (fail-safe — each step independently mergeable)

1. `types.ts` additions + `auditApi.uploadDataset` / `runDataAudit` + tests.
2. `csv.ts` + `validateMapping.ts` (pure) + tests.
3. `useCsvPreview` hook + test.
4. `UploadStep` → `MapStep` → `ReviewStep`.
5. Rewire `AuditWizard.tsx` orchestrator + integration test.

Each step keeps the app green; the wizard is fully wired only at step 5.
