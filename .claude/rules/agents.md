---
description: Multi-agent development discipline — delegate breadth to cheaper models, stay model-agnostic
paths:
  - "**"
---

# Multi-Agent Development Rules

Runway matters as much as velocity here. Every task that doesn't need the
primary model's judgment is a task burning budget it didn't need to.

- **Delegate breadth, not judgment.** Wide code exploration, doc/spec reading,
  repo-wide audits, boilerplate test scaffolding, and mechanical refactors go
  to a subagent on a cheaper/faster model. Architectural decisions, anything
  touching `security.md`/`backend.md` invariants, and the final review of a
  subagent's diff stay with the primary model.
- **Default cheap, escalate on evidence.** Start a delegated task on the
  cheapest model that could plausibly do it. Escalate to a stronger model only
  after a concrete failure (wrong output, missed edge case) — not preemptively
  "to be safe."
- **Model-agnostic by construction.** Don't write a prompt, spec, or workflow
  step that only works because of one model's quirks (a specific chain-of-
  thought style, a specific refusal pattern). Specs and subagent prompts
  should read the same and produce the same shape of result regardless of
  which model executes them — that's what keeps the fleet swappable as
  pricing/capability shifts.
- **Verify, don't trust.** A subagent's own "done" report is not evidence — per
  `CLAUDE.md`'s existing workflow rule, bound its lint/format steps to the
  files it touched and review the resulting diff yourself before it counts as
  shipped.
- **Bound every delegation.** A subagent prompt names the exact files/scope
  in play and the concrete deliverable (a spec doc, a diff, a test file) —
  never "figure out what needs doing," which just re-spends primary-model
  judgment one hop later at higher latency.
