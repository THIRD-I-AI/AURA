# S37 — Enterprise Frontend Redesign: Terminal Authority

**Date:** 2026-06-11 · **Issue:** opened at branch creation per §7 · **Owner:** Rohith
**Decisions made with user:** both surfaces under one design system · Terminal
Authority language with light certificates · full re-architecture of the
internal app around an auditor-workbench IA · big-bang branch execution.

## 1. Goal

Make AURA's frontend read as the best enterprise-grade audit product on the
market: a coherent design system across the customer-facing audit service and
the internal app, an information architecture that matches the S34
finance-auditor thesis, and a frontend security posture consistent with the
product's cryptographic claims.

## 2. Design language — "Terminal Authority"

Dark-first product UI that reads as built-by-security-engineers; the audit
*output* (certificates) renders on a light, print-safe theme — dark product,
light document.

### Tokens (CSS custom properties, `frontend/src/styles/tokens.css`)

| Token | Dark (product) | Certificate (light) |
|---|---|---|
| `--bg-base` | `#0a0e14` | `#fafaf8` |
| `--bg-surface` | `#0f141b` | `#ffffff` |
| `--bg-raised` | `#161d27` | `#f3f2ee` |
| `--border-default` | `#1d2530` | `#d9d7ce` |
| `--text-primary` | `#e6e9ef` | `#16181d` |
| `--text-secondary` | `#9aa4b2` | `#3d4148` |
| `--text-tertiary` | `#7d8590` | `#5c5f66` |
| `--signal` (success/verified) | `#22c55e` | `#1a7f3c` |
| `--info` | `#3b82f6` | `#2742ad` |
| `--warn` | `#f59e0b` | `#9a6700` |
| `--danger` | `#ef4444` | `#b42318` |

- Themes switch on `data-theme="dark" | "certificate"` at the nearest layout
  root. Certificate/verify pages set `certificate`; everything else `dark`.
- **Compatibility layer:** today's components consume `--accent`, `--green`,
  `--red`, `--card-bg`, `--border-default`, `--text-*`, `--space-*`,
  `--radius-*`, `--font-*`. tokens.css re-declares every legacy name as an
  alias of the new palette so unmigrated panels inherit the new look on day
  one — this is what makes big-bang tractable.
- Type: **Inter** (UI), **JetBrains Mono** (hashes, ids, amounts, evidence).
  Self-hosted via `@fontsource` packages — no external font CDN (CSP, privacy).
- Spacing: 4px scale (`--space-1..10`). Radii 4/6/10. Evidentiary callouts use
  a 3px accent-left border; verified states always pair color with a glyph
  (✓/⬢) — color is never the only signal (a11y).

### Primitives (`frontend/src/ui/`)

`Button` (primary/secondary/ghost/danger + visible disabled), `Card`,
`Badge` (status pills), `HashChip` (truncated mono hash + copy + optional
verify link), `StatusGlyph`, `DataTable` (dense, sticky header),
`EmptyState`, `Drawer`, `Stepper` (wizard), `KpiTile`, `ChartFrame`
(Recharts wrapper applying themed axes/grid/tooltip defaults). All styled via
CSS classes on tokens — no inline style objects in new code.

## 3. Customer audit surface (the product being sold)

Routes unchanged: `/`, `/audit/new`, `/audit/:jobId`, `/certificate/:hash`,
`/verify/:hash`.

- **Landing:** hero with one-line thesis + a live cryptographic proof strip
  (latest certificate hash + chain-intact check, fetched from the public
  verify endpoint); scenario cards w/ category chips; custom-audit and
  dashboard links become real buttons. Footer keeps the ED25519 statement.
- **Wizard:** the S36 drop-zone restyled onto primitives; `Stepper` replaces the
  text dots; map step uses `DataTable` with auto-encode notes as `Badge`s.
- **Progress:** live stage timeline (submitted → estimating → refuting →
  signing) on mono timestamps; terminal-style log pane.
- **Certificate:** `data-theme="certificate"`. Document layout (serif
  headline, mono evidence block, verified seal badge), print stylesheet,
  PDF/verify/JSON actions as documented affordances.
- **Verify:** the independent-verification page doubles as marketing — shows
  exactly what was recomputed and why it proves integrity.

## 4. Internal app re-architecture — auditor workbench

### IA (replaces the 13-item flat nav)

| Section | Route | Absorbs |
|---|---|---|
| **Engagements** (home) | `/app/engagements` | Dashboard (re-centered on audit runs: list, status, owner, certificate link) |
| **Evidence & Data** | `/app/evidence/*` | Files & Data, Chat (NL-SQL), Query History, Library, ETL Pipelines, Streaming, Lineage |
| **Findings** | `/app/findings` | **Audit Workbench** (Mounith's S35, PR #71) — already wired end-to-end to the S34b endpoints; re-homed + restyled onto the new primitives |
| **Certificates** | `/app/certificates` | Counterfactual artifacts, verify, bulk replay, Dashboards |
| **Monitoring** | `/app/monitoring` | Service health, UASR, LLM Cost, Streaming health |
| **Admin** | `/app/admin/*` | Settings, Webhooks, Agent, workspaces, keys |

- **Routing:** replace the `useState`-based page switching in `/app` with real
  React Router nested routes (deep-linkable, back-button-correct). `AppRoutes`
  tests extend to the new paths; old panel components mount under new routes.
- **Shell:** new `Sidebar` (6 grouped sections, every entry iconed — S36a's
  test generalizes), breadcrumb header, workspace switcher, **identity chip**
  showing the JWT subject + role (auditor decisions are signed as you — the
  UI should say who "you" is).
- **Panel migration:** all 13 existing panels keep their internals on day one
  (inheriting tokens via the compatibility layer) and are re-homed into the
  new routes. Engagements home and Findings are the two genuinely new
  compositions.

## 5. Frontend security architecture

1. **Auth layer (extends Mounith's S35):** Bearer plumbing +
   `ensureAuditorToken` already exist in `ApiClient`/`financialAuditService`.
   S37 adds: an **identity chip** in the shell header (JWT sub + role — signed
   decisions should show who "you" is), a 401 sign-in surface instead of
   silent failures, and a single token store consumed by all services.
2. **CSP:** strict `Content-Security-Policy` meta in `index.html` —
   `default-src 'self'`, no remote fonts/scripts; new code uses CSS classes
   (no style-src loosening beyond what legacy inline styles require at the
   start; tracked to tighten as panels migrate).
3. **Sanitizer reuse:** `sanitizeRecordHash` / `sanitizeApiBase` (Sec-6) are
   the only paths from remote data into URLs; `HashChip` consumes them.
4. **Lint guards:** eslint rules — forbid `dangerouslySetInnerHTML`, forbid
   `target="_blank"` without `rel="noopener noreferrer"`.
5. **No secrets in the bundle:** tokens in memory; workspace id stays the
   only localStorage key besides theme.

## 6. Testing & verification

- TDD per component/composition; new primitives each get focused tests.
- The existing 188 tests must pass at every commit on the branch — the
  compatibility token layer + unchanged panel internals make this feasible;
  route changes update `AppRoutes.test` + panel-mount tests deliberately.
- Visual verification with devtools screenshots at three checkpoints:
  customer surface done, shell+IA done, final.
- a11y: keyboard path through wizard and exception queue; axe pass on the
  five customer pages.

## 7. Execution (big-bang branch, user's call)

Branch `feature/s37-terminal-authority` off main after PR #73 (S36 polish) merges.
Internally ordered: tokens+primitives → customer surface → shell+routing →
panel re-homing → Findings/Engagements compositions → security layer →
final sweep. Rebase on main at least daily — the S35/S36 collision proved main
moves fast; nothing of Mounith's is in flight at branch time, but the
rebase discipline is non-negotiable on a big-bang branch. One squash-merge PR "Land Sprint S37"; sprint
claimed in SPRINTS.md + issue on branch creation.

**Out of scope:** backend changes; PDF renderer; new analytics features;
mobile-first layouts (responsive down to 1024px is in scope).

## 8. Open risks

- Big-bang branch lifetime — mitigated by daily rebase + always-green tests.
- Legacy inline styles cap CSP strictness initially (documented above).
- Chat/agent panels have the most bespoke styling; they migrate last and may
  land visually "compatible but not redesigned" in S36, queued for S37.1.
