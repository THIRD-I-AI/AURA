# CLAUDE.md — shared conventions for the AURA codebase

This file is auto-loaded by every Claude Code session in this repo.
Two developers (Mouni and a collaborator) are both using Claude Code
on this codebase. This document is the **shared source of truth** for
how we structure work — supersedes anything in any individual Claude's
local memory files.

If you're a new Claude session on this codebase: read this top-to-
bottom before writing code.

## How we structure work

### Sprint numbering

* **S<N>** — algorithmic / feature sprints (e.g., S17 Multi-Modal Fabric,
  S22 TMLE estimator). Each S<N> deepens one of the five enterprise
  pillars or adds analytic depth to the counterfactual engine.
* **S<N><letter>** — multi-part sprints when a single feature is too big
  for one bundle (e.g., S21a codegen + models, S21b operation methods,
  S21c AsyncClient).
* **P-<N>** — performance + audit-driven sprints. Triggered by a user
  audit listing concrete bottlenecks; each P-N closes one or more
  audit findings.
* **S<N>.<M>** — integration sprints that wire previously-shipped
  primitives into live paths (e.g., S20.1 wires Sprint 20a streaming
  primitives into the live operator loop). Distinct from S<N><letter>
  because the primitives must already be shipped.

### Commit style

* **One bundled commit per sprint.** Sweeping multi-area work goes in
  a single commit — see `feedback_commit_style` in memory.
* **Subject line:** `Land Sprint <id>: <one-line description>` or
  `Sprint <id> hotfix: <one-line description>`.
* **Body:** anchors (papers / RFCs cited), subsystems landed, key
  non-obvious decisions, verification summary, roadmap state update.
* **Co-author:** `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`
  on every commit you author with Claude help.

### Branching (active two-dev mode)

* **Default branch:** `main`. CI runs on every push.
* **NEW RULE as of 2026-05-19:** with two developers in flight, push
  to a feature branch and open a PR instead of pushing to `main`
  directly. Naming convention: `feature/<sprint-id>-<slug>` (e.g.,
  `feature/s22-tmle`, `feature/audit-burn-down`).
* **Single-author exceptions:** purely-additive coordination docs
  (this file, `docs/SPRINTS.md`, `docs/AUDIT_BURN_DOWN.md`) can land
  on `main` directly — they only ADD context, never modify code paths
  the other dev might be working on.

### Sprint claiming

* **Open a GitHub issue** before starting any sprint that touches code.
  Issue title: `Sprint <id>: <one-line goal>`. Assign yourself.
* **Reserve the sprint id in `docs/SPRINTS.md`** by adding a row under
  the "In Flight" section with your handle + the date.
* If you find the sprint already claimed, work on a different one or
  coordinate with the other dev via the issue.

## How we work

### Pre-push protocol

Before pushing ANY commit, run these locally — CI will block on them:

```sh
# Backend:
cd aurabackend
python -m ruff check --fix . --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823
python -m pytest tests/<the_file_you_touched>.py --tb=short
# Optionally a focused cross-sprint regression sample to catch surprises.

# Frontend (only if you touched it):
cd frontend
npx tsc --noEmit
npx eslint src --max-warnings 0
npx vitest run
```

See `feedback_ruff_isort_blank_lines` in memory for why ruff matters.

### Tests follow the "Tier A + Tier B" pattern for optional deps

* **Tier A** = pure-Python tests, no optional deps. Runs on the base
  backend CI lane (always).
* **Tier B** = tests that need an optional dep (Postgres, dowhy, faiss).
  Gated by `pytest.mark.skipif(not <dep>_available())` or by an
  `AURA_*_TEST_DSN` env var. **MUST** have a dedicated CI lane that
  installs the dep + runs the file. See `feedback_optional_dep_test_gating`.

### Persistence layer

* `aurabackend/api_gateway/persistence.py` owns the gateway's SQLAlchemy
  models, async engine, and session factory.
* **Lazy-init via `session_scope()`** — schema is created on first
  session use, NOT at import time and NOT solely from the FastAPI
  lifespan. This means tests that import a router directly without
  driving the lifespan still get working tables. Don't break this
  pattern; it's the fix for the P-1 hotfix bug.
* **Cross-router reads** of persistence-backed state MUST go through
  the repository functions in `persistence.py`, not legacy module
  attribute imports.

### The tool-render bug workaround

`git commit -m` and `git push` invoked through the Bash tool sometimes
return `undefined is not an object (evaluating 'H.replace')` — the
commit may or may not have actually run. Workaround:

1. First try: append `>out.log 2>&1 ; true` to redirect output.
2. Second try: `git push --quiet origin <branch>` for pushes.
3. Third try: write a Python wrapper to `runme.py` and invoke as
   `python runme.py`:

```python
import subprocess
r = subprocess.run(["git", "commit", "-F", ".commit_msg.txt"], capture_output=True, text=True)
with open(".commit.out", "w", encoding="utf-8") as f:
    f.write(f"rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
```

See `feedback_git_commit_tool_render_bug` for the full escalation
ladder.

## Where things live

### Repo structure (key paths)

```
aurabackend/
  api_gateway/        — FastAPI gateway on port 8000
    persistence.py    — Sprint P-1 SQLAlchemy layer (gateway-private)
    routers/          — endpoint modules
  counterfactual_service/  — port 8012, causal audit engine (S8-S16, S22+)
  shared/             — service_factory, audit_log, merkle, signing, …
  pipeline/streaming/ — S20a primitives (barrier, watermark, triggers, PID)
  scheduler_service/  — port 8004, distributed_queue.py from S20b
  uasr/               — Pillar 1 self-healing (S18 primitives)
  connectors/         — S17 multi-modal (FAISS + DuckDB spatial)
  tests/              — all pytest tests, single tier unless gated
sdk/                  — hand-written aura-counterfactual SDK
sdk_clients/          — auto-generated SDKs (S21a-c)
scripts/
  generate_sdk.py     — OpenAPI → typed Python client codegen
docs/
  SPRINTS.md          — public sprint registry
  AUDIT_BURN_DOWN.md  — playbook for performance audit findings
frontend/             — React + Vite + Vitest
.github/workflows/
  ci.yml              — 11-job CI sweep
```

### Sprint memory (local to each Claude, NOT shared)

Each developer's Claude maintains its own local memory at
`~/.claude/projects/.../memory/`. These files are NOT shared between
collaborators. If you discover something important, **write it into
`CLAUDE.md` or a `docs/` file** so the other dev's Claude can see it.

## Verification + CI

* CI sweep has **14 jobs** as of 2026-05-26: Backend Tests (Python 3.11 +
  3.12), Backend Lint (ruff), Bandit, Frontend Lint + Tests + Type Check,
  Scheduler Distributed (Postgres integration), SDK Codegen Sync,
  E2E Eval Gate (mock + real LLM), Causal Tests (dowhy + econml),
  Streaming Tests (aiokafka), Contract Tests (Schemathesis).
  CD workflow (`cd.yml`) builds and pushes 3 Docker tiers to GHCR.
  Nightly E2E (`nightly-e2e.yml`) boots the full compose stack.
* **All CI jobs must be green** before merging a PR. If a new optional
  dep needs a new lane, add it; never silently `pytest.mark.skipif()`
  and hope the gate catches it.
* **SDK Codegen Sync** runs `scripts/generate_sdk.py` and `git diff
  --exit-code sdk_clients/` — if you regenerate locally, commit the
  result. Drift breaks the build.

## What NOT to do

* **Don't push to `main` directly while two devs are in flight.** Use a
  feature branch + PR. (Single-dev coordination-docs are the
  exception — see Branching section.)
* **Don't add a feature flag for "backwards-compatibility"** when you
  can just change the code. We trust internal call sites.
* **Don't write comments explaining WHAT the code does** when good
  identifiers do that already. Comments are for WHY: hidden constraints,
  subtle invariants, workarounds for specific bugs. Each comment must
  earn its keep.
* **Don't add error handling for cases that can't happen.** Validate at
  system boundaries (user input, external APIs). Trust internal code.

## Pointers

* `docs/SPRINTS.md` — what's done, what's in flight, what's next.
* `docs/AUDIT_BURN_DOWN.md` — playbook for the performance audit
  findings (Mouni's collaborator is driving these).
* `ARCHITECTURE.md` — repo + service topology (existing).
* `ENTERPRISE.md` — deployment + compliance posture (existing).
* `STREAMING_FOUNDATIONS.md` — formal math behind the streaming
  primitives (existing).

Last updated 2026-05-26 by Mouni. CI expanded to 14 jobs (S28-S30).
Update the date when you make material changes to this file.
