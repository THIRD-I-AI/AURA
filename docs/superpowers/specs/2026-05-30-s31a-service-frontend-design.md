# S31a — Service Front Door (Frontend) — Design Spec

**Date:** 2026-05-30
**Track:** S31a (YC demo — frontend) · branch `feature/s31a-service-frontend`
**Owner:** Rohith
**Pairs with:** S31b (backend — Mounith): demo scenarios, persistent ED25519 key,
IV estimator, `/demo` endpoints. Spec: `docs/superpowers/specs/2026-05-29-s31b-audit-engine-design.md`.

> Sprint-number note: the labels `S31a`/`S31b` are reused from earlier
> test-coverage work in the repo. We keep the branch name from the coordination
> doc; commit subjects say "S31a (YC demo)" to avoid registry confusion.

---

## 1. Goal

Reposition AURA's frontend as an **AI-native audit *service*, not a tool**. A
visitor lands on a public page, picks a regulated-compliance scenario, watches
the audit run its battery of causal estimators live, and receives a **formal,
cryptographically-verifiable certificate** anyone can independently verify at a
shareable URL.

This is **assembly against a frozen backend contract** (S31b §5), not green-field
backend work. S31a is purely `frontend/` — no file overlap with S31b, merges
independently.

## 2. Compliance constraint (non-negotiable)

AURA is sold into **regulated industries — banking, insurance, healthcare**. The
frontend must reflect a high-security compliance posture:

- The public surfaces (`/`, `/verify/:hash`) mount **zero dashboard auth context
  or data-fetching hooks** — an outside regulator/investor hitting `/verify`
  reaches only the verification endpoint, nothing else.
- The verify page **does not trust the artifact's self-reported status**; it
  renders the verdict from the server-recomputed `GET /artifacts/{hash}/verify`
  (mirrors how the S31b SDK anchors against the STH, not self-attested roots).
- **No secrets, tokens, or PII** in the public bundle. The certificate exposes
  only the record hash, the public-key-verified badge, and the key source.

## 3. Architecture — app shell & routing

Introduce `react-router-dom` at the root (`main.tsx`). The existing dashboard is
moved **wholesale** under a catch-all and otherwise untouched.

```
<BrowserRouter>
  <Routes>
    <Route path="/"                  element={<AuditFrontDoor/>} />   // public, chrome-free
    <Route path="/audit/new"         element={<AuditWizard/>} />      // public guided form
    <Route path="/audit/:jobId"      element={<AuditProgress/>} />    // live estimator checklist
    <Route path="/certificate/:hash" element={<CertificatePage/>} />  // formal certificate
    <Route path="/verify/:hash"      element={<VerifyPage/>} />       // public verification
    <Route path="/app/*"             element={<AppInner/>} />         // existing dashboard, state-nav intact
  </Routes>
</BrowserRouter>
```

Decisions:
- **`AppInner` (current `App.tsx` body) moves under `/app/*`.** Its internal
  `currentPage` state-nav keeps working as-is — zero churn to the 15+ existing
  pages. Only the mount location changes.
- **Two shells:** a new lightweight `PublicShell` (logo + minimal footer, no
  sidebar, no auth/data hooks) wraps public routes; the dashboard keeps its
  existing `Layout`.
- **The existing in-app Counterfactual tab stays** for power users; the new
  front door is the service face. Pure addition + a mount move, no deletion.
- Grep for hardcoded internal links/redirects assuming root-mount; prefix with
  `/app` as needed (expected to be few — nav is state-based, not URL-based).

## 4. Components

All under `frontend/src/audit/` so S31a is self-contained and reviewable in
isolation.

| Unit | File | Purpose | Depends on |
|---|---|---|---|
| Front door | `audit/AuditFrontDoor.tsx` | Hero + scenario grid. `GET /demo/scenarios` → card per scenario; click → `POST /demo/{id}` → `/audit/{job_id}` | `auditApi`, `PublicShell` |
| Wizard | `audit/AuditWizard.tsx` | Guided multi-step form (dataset → treatment → outcome → confounders → review) replacing raw-JSON editor; submit → job → `/audit/{job_id}` | `auditApi` |
| Progress | `audit/AuditProgress.tsx` | Estimator checklist; polls `GET /jobs/{job_id}`, renders `estimates[]`/refuters/critic as they fill; on `succeeded` → `/certificate/{hash}` | `auditApi`, `useJobPolling` |
| Certificate | `audit/Certificate.tsx` | Formal credential, **pure presentational**: verdict headline + hash + ED25519 badge + key source + PDF + verify link. Takes an `artifact` prop | — |
| Certificate page | `audit/CertificatePage.tsx` | Route wrapper: loads artifact by `:hash`, renders `<Certificate>` | `auditApi`, `Certificate` |
| Verify page | `audit/VerifyPage.tsx` | Public verification: `GET /artifacts/{hash}/verify`, renders `<Certificate readOnly>` + live "signature checked against public key" banner | `auditApi`, `Certificate` |
| Public shell | `audit/PublicShell.tsx` | Chrome-free wrapper; mounts **none** of the dashboard's auth/data hooks | — |
| API client | `audit/auditApi.ts` | Thin typed wrapper over the 6 frozen S31b endpoints; reuses `API_BASE_URL` from `services/api.ts` | `services/api.ts` |
| Job polling hook | `audit/useJobPolling.ts` | Polls `/jobs/:id` on interval w/ backoff; stops on terminal state; surfaces `degraded` | `auditApi` |

Design rationale:
- **`Certificate.tsx` is pure presentational, reused by three surfaces** (post-run
  certificate, public verify, dashboard embed). One source of truth for "what a
  sealed audit looks like."
- **`useJobPolling` isolates the only stateful/timing-sensitive logic** into a
  testable hook; the progress UI is a dumb render of polled state.

## 5. UI directions (validated via visual brainstorming)

- **Front door:** hero value-prop + **scenario grid** (gallery of cards). Scales
  as Mounith registers scenarios #2–4 — each is just another card.
- **Live progress:** **estimator checklist** — vertical list of the 7 estimators,
  each row queued → running → done with point estimate + CI sliding in;
  refuters + adversarial critic as a sub-section; final "sealing certificate" row.
- **Certificate:** **formal credential** — bordered document, verification seal,
  plain-English verdict headline leading, then hash + ED25519 badge + key source
  as the trust layer, with "Download PDF" and "Verify independently" actions.

## 6. Data flow

**Instant scenario path (demo wow):**
```
/ (AuditFrontDoor)
  GET /demo/scenarios → scenario cards
  click card → POST /demo/{id} → { job_id, degraded } → navigate(/audit/{job_id})
/audit/:jobId (AuditProgress)
  useJobPolling: GET /jobs/{jobId} ~800ms
    queued|running → animate checklist from artifact.estimates[]
    degraded:true  → subtle "served verified cached result" note
    succeeded      → navigate(/certificate/{artifact.audit_record_hash})
/certificate/:hash (CertificatePage)
  <Certificate artifact={…}/>
    Download PDF        → GET /artifacts/{hash}/report.pdf
    Verify independently→ /verify/{hash}
```

**Custom audit path:** `/audit/new` wizard → builds `CounterfactualQuery` →
job-submit → `/audit/{job_id}` → same progress + certificate screens.

**Public verify path (cold, no session):**
```
/verify/:hash (VerifyPage)
  GET /artifacts/{hash}/verify → { verified, signature_status, signing_key_source, reason }
  (optional) GET /public-key   → show the key the signature was checked against
  <Certificate artifact readOnly verifyResult={…}/>
```

The progress screen drives entirely off the polled snapshot — no client-side
audit state to drift. Certificate/verify are pure reads. The whole S31a surface
is stateless between requests: refresh-safe, deep-link-safe, session-free.

The `audit_record_hash` is the routing key for both `/certificate/:hash` and
`/verify/:hash` — **the certificate URL is itself the verifiable artifact
identity.**

## 7. Error / degraded handling

Each is an explicit render, never a blank screen or crash:
- Scenario fetch fails → retry card (not a broken grid).
- `POST /demo/{id}` fails → inline toast, stay on grid.
- Job poll `state: failed` → clear "audit could not complete" panel with `error`
  + "try again" back to `/`.
- `degraded: true` → green-but-noted "served a verified cached result" — still a
  valid certificate (S31b fail-safe is first-class UI, not an error).
- Verify of unknown/invalid hash → explicit **"NOT verified"** with `reason` —
  a feature for a compliance tool, not an error to hide.
- PDF 501 (reportlab absent) → "PDF unavailable" disabled state; on-screen
  certificate still fully usable.

## 8. Backend contract consumed (frozen — S31b §5)

```
GET  /counterfactual/demo/scenarios          → { scenarios: [{id, title, vertical, description}] }
POST /counterfactual/demo/{scenario_id}       → { job_id, scenario_id, degraded }
GET  /counterfactual/jobs/{job_id}            → { job_id, state, artifact, error }
GET  /counterfactual/artifacts/{hash}/verify  → { record_hash, verified, signature_status, signing_key_source, reason }
GET  /counterfactual/artifacts/{hash}/report.pdf → application/pdf
GET  /counterfactual/public-key               → { public_key_pem, key_source }
```

`artifact` includes: `audit_record_hash`, `estimates[]`, `refutations[]`,
`signature_status`, `signing_key_source`, `rendered`.

## 9. Testing (all Tier A — pure frontend, fetch mocked)

Vitest + Testing Library, matching the existing `__tests__` convention. Runs on
the existing Frontend Tests CI lane — no new infra.

- `auditApi.test.ts` — each of the 6 endpoints: URL/method vs `API_BASE_URL`,
  response parsing, error mapping.
- `useJobPolling.test.ts` — queued→running→succeeded transitions; stops on
  terminal; surfaces `degraded`; handles `failed`.
- `AuditFrontDoor.test.tsx` — renders a card per mocked scenario; click triggers
  `POST /demo/{id}` + navigation.
- `AuditProgress.test.tsx` — running snapshot → checklist states; succeeded →
  navigate to certificate.
- `Certificate.test.tsx` — verdict, hash, ED25519 badge, key source; `readOnly`
  hides actions; verified vs not-verified states.
- `VerifyPage.test.tsx` — verified hash → trust banner; invalid → NOT-verified +
  reason.

Pre-push (repo protocol): `npx tsc --noEmit`, `npx eslint src --max-warnings 0`,
`npx vitest run`.

## 10. Build order (fail-safe — ship whatever's done)

1. **Router + shells:** `react-router-dom`, `PublicShell`, mount `AppInner` under
   `/app/*`. → app still works, dashboard at `/app`.
2. **`auditApi` + `useJobPolling`** + tests. → typed contract layer.
3. **Front door** (scenario grid) + **Progress** (checklist). → one-click audit
   runs and animates.
4. **Certificate** component + page. → the payoff, screenshot-ready.
5. **Verify page** (reuses Certificate). → independent verification.
6. **Wizard** (custom-audit form). → JSON-editor replacement.

Each step is independently mergeable and leaves the app working.

## 11. Non-goals (YAGNI)

- No websocket/SSE progress — poll the existing `/jobs/{id}` (matches S31b §9).
- No converting the entire dashboard to URL routes — only the S31a surfaces get
  real routes; the dashboard stays state-nav behind `/app/*`.
- No new auth/login flow for the public pages.
- No backend changes — consume the frozen S31b contract only.
- No deletion of the existing Counterfactual tool page.
