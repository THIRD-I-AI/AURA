# CLAUDE.md — shared conventions for the AURA codebase

Auto-loaded in every Claude Code session in this repo. Two developers (Mouni and
a collaborator) work here; this is the **shared source of truth** and supersedes
any individual Claude's local memory files.

**Context is layered — keep this file under ~150 lines.** Anything longer belongs
in one of the other two layers:

| Layer | Loads | Holds |
|---|---|---|
| this file | always | commands, execution mode, hard constraints, shared process |
| `.claude/rules/*.md` | only when you touch matching paths | `backend.md`, `frontend.md`, `testing.md`, `security.md`, `performance.md`, `agents.md` |
| `CLAUDE.local.md` | always, git-ignored | machine-specific overrides (local DSNs, paths) |

# 🛠️ Tech Stack & Core Commands

Python 3.11/3.12 FastAPI backend · React + Vite + Vitest frontend · SQLite in
dev and on the free-tier box, Postgres in prod · DuckDB for analytics.

```sh
# Backend (aurabackend/). Use the REPO-ROOT .venv — aurabackend/.venv holds
# broken 0-byte stubs, so a local rc=0 from it is meaningless.
cd aurabackend
../.venv/Scripts/python.exe -m pytest tests/<file>.py --tb=short   # test
../.venv/Scripts/python.exe -m ruff check --fix .                  # lint

# Frontend (frontend/)
npm run test         # vitest run
npm run lint         # eslint .
npm run build        # tsc -b && vite build — this IS the typecheck CI runs
npm run dev:fresh    # vite --force; required after any branch switch

# Migrations
cd aurabackend && ../.venv/Scripts/python.exe -m alembic upgrade head
```

Full pre-push gate, tier pattern, and edge-case requirements: `.claude/rules/testing.md`.

# 🎯 Execution Mode & Workflow

1. **Plan before code.** For anything beyond a one-line fix, propose the strategy
   first and state a 2-step verification plan — how you will prove it works.
2. **Do not over-engineer.** No abstraction, interface, factory, or config knob
   that was not requested. Climb to the simplest rung that holds.
3. **Minimal, surgical diffs.** Do not rewrite working code. A 3-line change beats
   a rewrite that reads better.
4. **Write code and its assertions together**, so the change carries its own
   verification loop.
5. **Delegate breadth to subagents** — wide code exploration, doc reading, audits.
   Bound their lint/format steps to the files they touched, and verify the
   resulting diff yourself; a worker's own scope report is not evidence.

# 🛑 Hard Constraints

- **NEVER** edit files outside the current task's scope without asking.
- **NEVER** commit `.env`, secrets, keys, or production credentials.
- **NEVER** mock an external call when a local test environment exists.
- **NEVER** use `--no-verify` or otherwise bypass hooks. If a hook fails, fix the cause.
- **Stage explicit paths.** `git add -A` has swept scratch files into a commit here,
  and `git status` shows ~594 phantom CRLF-only diffs — the real blast radius is
  `git diff --ignore-cr-at-eol --numstat`.
- **If a build or test fails, loop and fix it before reporting done.** Report a
  failure you cannot fix; never report a pass you did not observe.
- **Don't add a compatibility feature flag** when you can just change the code.
  We trust internal call sites.
- **Don't comment WHAT the code does** — good identifiers do that. Comments are for
  WHY: hidden constraints, subtle invariants, workarounds for specific bugs.
- **Don't handle errors that can't happen.** Validate at system boundaries (user
  input, external APIs); trust internal code. But an error path must name the
  *true* cause — a handler that maps a broad exception to one narrated reason
  will eventually lie.

# 🔄 Compaction Rule

When `/compact` fires, ALWAYS preserve: active sprint/feature roadmaps, breaking
changes, in-flight PR and CI state, and the exact build verification commands
above. Run `/compact` intentionally between large tasks rather than waiting for
the auto-trigger.

# 🗺️ Architecture Map

Entry points only — everything else is derivable from the tree. Do not grow this
into a directory listing; the last one rotted and named a port that had not
existed for several sprints.

- **Entry points** — `aurabackend/api_gateway/main.py` (FastAPI gateway, :8000,
  the only backend surface the frontend talks to); `aurabackend/uasr/service.py`
  (:8009, self-healing MAPE-K loop, separate container); `frontend/src/main.tsx`.
- **Core logic** — `aurabackend/api_gateway/routers/`, `aurabackend/uasr/`,
  `aurabackend/pipeline/`, `aurabackend/shared/`.
- **Non-obvious:** the counterfactual / financial-audit engine is NOT a separate
  service — it runs in-process inside the gateway.

# 🌿 Process (two-dev mode)

- **Branching** — default branch `main`; CI runs on every push. With two devs in
  flight, push a feature branch and open a PR rather than pushing to `main`.
  Naming: `feature/<sprint-id>-<slug>`. Exception: purely-additive coordination
  docs (this file, `docs/SPRINTS.md`, `docs/AUDIT_BURN_DOWN.md`) may land directly.
- **Sprint claiming** — open a GitHub issue titled `Sprint <id>: <goal>`, assign
  yourself, and reserve the id in `docs/SPRINTS.md` under "In Flight". If it is
  already claimed, coordinate via the issue.
- **Sprint ids** — `S<N>` feature · `S<N><letter>` multi-part · `P-<N>`
  performance/audit-driven · `S<N>.<M>` integration of already-shipped primitives.
- **Commits** — Conventional Commits (`fix(frontend): …`, `feat(saas): …`). Body
  carries anchors cited, subsystems landed, non-obvious decisions, and verification.
  Co-author line names the model that actually wrote it.
- **All CI jobs green before merging.** The job list lives in `.github/workflows/`
  — read it there, any copy here goes stale. **SDK Codegen Sync** runs
  `scripts/generate_sdk.py` with `git diff --exit-code sdk_clients/`; commit
  regenerated output or the build breaks.
- **Sprint memory is local, not shared.** Each dev's Claude keeps its own memory
  under `~/.claude/projects/.../memory/`. If you learn something the other dev
  needs, write it into this file, a `.claude/rules/` file, or `docs/`.

# 📚 Pointers

`docs/SPRINTS.md` (done / in flight / next) · `docs/AUDIT_BURN_DOWN.md` ·
`ARCHITECTURE.md` · `ENTERPRISE.md` · `STREAMING_FOUNDATIONS.md`.
Git tool-render bug escalation ladder: `feedback_git_commit_tool_render_bug` in memory.

Last restructured 2026-08-21 into the three-layer layout (root / `.claude/rules/`
/ `CLAUDE.local.md`). Update the date on material changes.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
