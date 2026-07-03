# AURA Workbench — Living State Document

> **Purpose:** cross-session memory anchor. Claude sessions lose their middle context to
> compaction; this file is the durable truth. **Every session that changes the workbench
> MUST update this file in the same commit.** A SessionStart hook injects the DIGEST
> section below, so keep it short and current.

## DIGEST (session-load — keep ≤30 lines)

- **Branch:** `feature/workbench-redesign` → PR **#139** (stacks on #138 ← #136 ← #135 → main).
- **What this is:** the user-authored Claude Design workbench (source: `docs/design/aura-workbench/`,
  design project `f10348fb-…` via DesignSync; target file "AURA Workbench (Interactive) copy.dc.html")
  ported to `frontend/src/workbench/` at route `/workbench`. It is becoming THE app (user directive:
  "everything is one" — merge classic app INTO it; classic `/app` is transitional).
- **Files:** `Workbench.tsx` (shell+cockpit, single component), `workbench.css` (.aw tokens dark/light),
  `viewRegistry.ts` (nav→classic-page map), `views.tsx` (ViewHost: providers+error boundary),
  `Workbench.test.tsx` (8 tests). Route in `AppRoutes.tsx`.
- **True now:** pinned-chrome scroll (shell height:100vh); NO dummy data anywhere (real
  services or honest empty/offline states); real JWT login (email path; SSO buttons = demo);
  15 classic pages mount in-shell via registry; ⌘K palette; live ledger chip w/ OFFLINE degradation.
- **GAP TO DESIGN (top priority, user-flagged 2026-07-03):** ①mounted classic pages don't use
  .aw design language (look old inside new shell) ②design's per-view compositions (screenshots
  54–65) partially replaced by classic pages/stubs ③panels look empty because backing services
  are down — **start counterfactual :8012 + seed real data = "working model, not empty prototype"**.
- **Open (smaller):** back/forward = URL sync for nav (not done); user menu/profile in topbar (not
  done); per-profile data isolation (workspace pinned 'default' in dev — backend `require_tenant`
  exists, frontend must scope); boot stages → real /health probes; Terminal/Certificates/Scheduler/
  Metadata Store stubs; commander answer-phrasing needs gateway restart to take effect.
- **Run:** vite `npm run dev:fresh` (VITE_COMMANDER_ENABLED=true) :5173; gateway :8000 up;
  counterfactual :8012 usually DOWN. Tests: `npx vitest run src/workbench/…`; local tsc/eslint per repo.

## Component map (Obsidian-style)

- [[Workbench.tsx]] — shell: login → boot → app. Owns theme, nav state, palette, toast,
  cockpit panels (stats / Ask AURA / forensic-audit card / healing / pipelines / lineage /
  history / session-events feed). Cockpit = live board; other navs → [[views.tsx]].
  - consumes: `chatService.streamMessage` (commander SSE), `authService.login`,
    `healingService.pending|approve|reject` (S41), `analyticsService.getQueryHistory`,
    `streamingService.list`, `uploadService.getUploadedFiles`,
    `GET {API}/counterfactual/audit/ledger/verify`, `GET {API}/counterfactual/audit/financial/demo`, `GET /health`.
- [[viewRegistry.ts]] — nav name → lazy classic page. 15 entries; `needsSetPage` pages get a
  `setCurrentPage` shim translated by `PAGE_ID_TO_NAV`.
- [[views.tsx]] — `ViewHost`: AuraProvider + ToastProvider + ToastContainer + `ViewBoundary`
  (error boundary; failure = honest notice, resets on nav change).
- [[workbench.css]] — `.aw[data-theme]` token sets (from the design helmet), fonts
  (Space Grotesk / Instrument Sans / JetBrains Mono), keyframes, panel/nav/input classes.
- Classic pages (mounted, NOT yet reskinned): FilesAndData, QueryHistory, Library, Dashboards,
  Lineage, Cost, AgentPanel(=Connectors), PipelinesPanel, StreamingPanel, WebhooksPanel,
  Counterfactual, AuditService, ExceptionQueue, HealingQueue, ChatInterface.

## Retrospect (what works / what doesn't — 2026-07-03)

**Working, verified live:** login (real JWT via /auth/token) → boot → cockpit; pinned scroll;
real data in stats (upload count confirmed), healing queue (real S41), history, pipelines;
forensic-audit card runs the real signed demo when :8012 is up; in-shell mounting verified for
Files & Data, Query History, Dashboards, Healing Queue (no boundary trips); ⌘K; dark/light;
8/8 tests, tsc+eslint clean. ECC react-reviewer fixed 2 real bugs (toast timer leak, stale runCf).

**Not working / gaps:** the three design gaps above; back button; user menu; session isolation
(single 'default' workspace in dev mode); ledger/audit reads fail while :8012 down (honest
OFFLINE shown); classic-page visual mismatch is the loudest complaint.

**Process lessons (persisted in ~/.claude memory):** verify visuals at the user's real viewport
+ fresh dev server, side-by-side vs design; never silence a gate (eslint rc=2 slipped through a
`>/dev/null` once); stale 7-day vite serves mangled bundles — `npm run dev:fresh`.

## Next actions (ordered)

1. **Design-language pass on mounted pages:** scope classic page CSS under `.aw` overrides
   (map classic tokens → .aw vars in workbench.css) so mounted views inherit the design skin.
2. **Working model:** start counterfactual :8012 (repo-root `.venv`; Windows OK for serving),
   re-run `warm_demos` if signatures stale, upload a demo dataset, define one pipeline —
   cockpit then shows a LIVING board with zero dummies.
3. **Port the design's remaining view compositions** from the other 3 design pages
   (Dashboard Explorations / Interactive / Prototype — pull via DesignSync).
4. **URL-synced nav** (pushState + popstate) → back/forward works; deep links `/workbench?view=`.
5. **User menu** (decode JWT payload → initials/email, sign out) + workspace switcher →
   per-profile isolation (backend org_id scoping already exists — S37 `require_tenant`).
6. Boot stages → real /health probes; Terminal in-shell (dockview); Certificates/Scheduler/
   Metadata Store views designed then built.

## ENTERPRISE ROADMAP (user decisions, 2026-07-03 — binding)

User-confirmed direction ("we both have to make this work"):
1. **Deploy:** Cloud SaaS multi-tenant FIRST; on-prem/air-gapped profile stays a premium tier.
2. **Collaboration v1 = signed approval chains:** N-of-M Ed25519-signed sign-offs on pipeline
   deployments, healing actions, and audit findings, chained into the per-tenant ledger.
   Builds directly on the S41 HITL + audit_ledger machinery. Real-time presence = later.
3. **Connectors = universal layers, not bespoke list** (user: "work with anything"):
   ① SQLAlchemy-dialect connector → any SQL database via URL (Postgres/MySQL/BigQuery/
   Snowflake/Redshift/Oracle/MSSQL/…) ② generic REST/OpenAPI connector (auth profiles:
   OAuth2/API-key/basic) → most SaaS incl. Salesforce/NetSuite as CONFIGURATIONS not code
   ③ files/object storage ④ Kafka/webhooks (exist) ⑤ connector SDK for the long tail;
   evaluate wrapping an open-source connector catalog (e.g. Airbyte protocol) for breadth.
4. **Auth = every corporate IdP** (user: "login with anything their company provides"):
   generic OIDC + SAML 2.0 + SCIM provisioning ≈ Entra/Okta/Google/Ping/Auth0/Keycloak/….
   MFA + passkeys on the password path. Wire real IdP flows behind the existing login visuals.
   → **OIDC SLICE 1 SHIPPED (2026-07-03, branch `feature/enterprise-oidc`):** `shared/oidc.py`
   (PKCE S256, single-use TTL state, JWKS signature verification, org mapping claim→tid/hd→
   email-domain) + `/auth/oidc/{status,login,callback}` in the auth router + frontend
   `/auth/sso` fragment handoff + status-aware workbench SSO buttons. 6 backend tests.
   Configure via AURA_OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET/REDIRECT_URI. KNOWN LIMIT:
   in-process state store = single replica; Postgres state store + SAML + SCIM = next.
   **HARDENED (5 review findings fixed, 8 tests):** JWT never in URLs (60s single-use
   handoff code + POST /auth/oidc/exchange); state cookie-bound to browser (login-CSRF);
   redirect dest must be absolute http(s); JWKS fetch off-loop w/ timeout + 300s key cache
   (rotation-safe) + 1h discovery TTL; tenant mapping FAIL-CLOSED (org claim or
   email_verified-only domain — unverified email cannot choose a tenant).

**Enterprise scorecard (honest, 2026-07-03):** ~70% — security/audit/tenancy/self-healing/
deploy = STRONG (the differentiated core is built + tested); missing = SSO (visual only),
RBAC beyond admin/auditor, approval chains, universal connectors, observability/SLOs,
load testing. All known-quantity engineering. Reliability bar: every new system ships
with tests + CI lane + failure-mode handling (fail-closed), per repo convention.

## How to resume in a fresh session

Read this file first. Then: `git log --oneline -10` on `feature/workbench-redesign`,
`~/.claude/projects/C--Users-mouni/memory/project_aura_audit_ledger.md` (workbench section),
and the design source in `docs/design/aura-workbench/`. PR #139 description has the shipped log.
