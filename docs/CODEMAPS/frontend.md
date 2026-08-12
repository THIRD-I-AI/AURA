<!-- Generated: 2026-07-28 | Files scanned: 45 | Token estimate: ~940 -->

# AURA — Frontend Codemap

React 19 + Vite 8 + TypeScript ~5.9 + Tailwind v4 + shadcn/ui (`new-york`
style, primitives in `src/components/ui-kit/` — the mandatory design system
per `frontend/CLAUDE.md`; do NOT use legacy `src/components/ui/`).

## Route tree (`frontend/src/AppRoutes.tsx`)

```
/                    PublicShell > AuditFrontDoor        (public)
/login, /signup      PublicShell > AuthForm               (public)
/auth/sso            SsoCallback                          (OIDC fragment handoff)
/audit/new           PublicShell > AuditWizard             (public)
/audit/:jobId        PublicShell > AuditProgress           (public)
/certificate/:hash   PublicShell > CertificatePage         (public)
/verify/:hash        PublicShell > VerifyPage              (public)
/workbench           ProtectedRoute > Workbench            (lazy)  ← the one authenticated app
/app/terminal/*       ProtectedRoute > TerminalWorkspace    (lazy)  ← sibling cockpit
/app/*               ProtectedRoute > Navigate → /workbench   (legacy shell REMOVED — App.tsx deleted,
                                                                 only orphaned App.css remains on disk)
```

`ProtectedRoute` (`src/auth/`) gates `/workbench` and `/app/terminal`.

## Workbench cockpit (`src/workbench/Workbench.tsx`)

Single-page cockpit: boot sequence → `view: 'boot' | 'app'`, nav state,
⌘K palette, live System Radar. Nav groups (`NAV_GROUPS`):

```
WORKSPACE — Cockpit · Terminal · Ask AURA · Dashboards · Library · Query History
AUDIT     — Audit Workbench · Counterfactuals · Certificates · Exception Queue
OPERATE   — Pipelines · Streaming · Healing Queue · Scheduler · Webhooks · Cost
DATA      — Connectors · Files & Data · Lineage · Metadata Store
```

`Cockpit` renders inline (`isCockpit`); every other nav item resolves through
`src/workbench/viewRegistry.ts` (`VIEW_REGISTRY`, `PAGE_ID_TO_NAV` for legacy
`setCurrentPage` calls). Native ui-kit/Tailwind panels (`src/workbench/panels/`):
`AskAuraPanel · ConnectorsPanel · CostPanel · DashboardsPanel ·
FilesAndDataPanel · HealingQueuePanel · LibraryPanel · LineagePanel ·
QueryHistoryPanel · StreamingPanel · WebhooksPanel`. Still-embedded classic
pages pending native rebuild (`src/pages/`, `src/components/HITL/`):
`PipelinesPanel · Counterfactual · AuditService · ExceptionQueue`.

## Terminal cockpit (`src/terminal/`)

`dockview-react` panel grid + `@xyflow/react` Constellation lineage graph.
`TerminalWorkspace.tsx` (shell) · `CockpitProvider.tsx` · `CockpitTopBar.tsx` ·
`TerminalCommandPalette.tsx` · `TerminalRadarRail.tsx` ·
`MobileTerminalStack.tsx` (<860px, `useMediaQuery.ts`) · `layoutStore.ts`.
Panels (`src/terminal/panels/registry.ts` → `PANEL_REGISTRY`, `PanelId`):
`pipeline → PipelinePanel · audit → AuditPanel · query → QueryPanel ·
datasets → DatasetsPanel · findings → FindingsPanel · livefeed → LiveFeedPanel ·
constellation → ConstellationPanel`. Constellation internals in
`src/terminal/constellation/`: `AuraNode.tsx`, `deckModel.ts`, `layout.ts`
(React Flow + `d3-force`).

## API client (`src/services/api.ts`)

`API_BASE_URL = ROOT_BASE_URL + '/api/v1'`; `ROOT_BASE_URL` = sanitized
`VITE_API_URL` (or same-origin empty string in prod behind nginx), with a
`localStorage('apiUrl')` override validated by `sanitizeApiBase()` (blocks
`javascript:`/`data:` injection — CodeQL js/xss-through-dom fix).
`sanitizeRecordHash()` allow-lists 64-char lowercase sha256 hex for anchor
hrefs. Every request carries `X-Workspace-Id` (from `getCurrentWorkspaceId()`,
persisted `localStorage('aura.workspaceId')`, default `'default'`).

## Auth flow

- `authService.login(email, pw)` → `POST /auth/token` → JWT stored via
  `setAuthToken()` (`localStorage('aura.authToken')`) → decoded client-side
  (`decodeAuthToken`) for display only; server re-verifies on every call.
- `authService.register()` → `POST /auth/register` → auto-login.
- `AuthUser` claims: `sub, email, name, role, org_id` (org_id = tenant).
- SSO: generic OIDC (`/auth/oidc/*`), single-use code exchanged via
  `POST /auth/oidc/exchange` — JWT never transits a URL fragment/query.

## Tests

Vitest + Testing Library (`src/test/`) + Playwright e2e
(`npm run test:e2e`). Verify gate: `npx tsc --noEmit && npx eslint src
--max-warnings 0 && npx vitest run`.
