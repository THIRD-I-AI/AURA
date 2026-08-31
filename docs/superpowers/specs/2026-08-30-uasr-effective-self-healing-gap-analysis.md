# UASR Effective Self-Healing — Gap Analysis & Next-Sprint Candidates

> **Status: proposed, unclaimed.** Not yet a numbered sprint — open a GitHub
> issue titled `Sprint S46: <goal>` (or the next free id) and self-assign per
> `docs/SPRINTS.md` → "How to update this file" before starting build work.
> Produced by a read-only audit of `aurabackend/uasr/` against
> `docs/SPRINTS.md` sprint history and `docs/SUPERVISED_SELF_HEALING.md`, and
> lightly corrected against the working tree at time of writing.

**Goal this feeds:** UASR should reduce, not just report, the manual
data-engineering toil of watching pipelines for drift — real autonomous
self-healing on data-contract drift, not a monitoring dashboard that pages a
human for everything.

## Shipped & reachable today

- **Core MAPE-K loop** (S18, S20.1) — Monitor → Analyze (KL-divergence,
  schema, semantic drift) → Plan → Execute (sandbox-validated) → Knowledge,
  live via Kafka or `POST /uasr/ingest`.
- **Risk-tiered human-in-the-loop** (S41, PR #99) — a validated *template*
  shim at LOW/MEDIUM severity auto-deploys only when
  `UASR_RISK_TIERED=true` and mode is AUTO; everything else (LLM/fallback
  shims, HIGH/CRITICAL severity, SUPERVISED/MONITOR_ONLY mode) holds in
  `PENDING_APPROVAL`, fail-closed. `GET/POST /uasr/recovery/*` + Healing
  Queue dashboard tab.
- **Numeric value-healing, now reachable** (this session, PR #256) —
  `UASR_NUMERIC_SEMANTICS` / `UASR_NUMERIC_AUTO_HEAL` resolve into
  `MAPEKConfig` via `runtime_config.numeric_heal_flags()` +
  `service._mapek_config()`; `GET /uasr/deployment` reports the resolved
  state. Closes exactly the class of gap below for one detector.
- **Repair admission & horizontal scale** — local or Redis-distributed
  concurrency bounds (`UASR_REPAIR_BACKEND`); in-memory or Redis state
  backend so cold replicas inherit peer baselines.
- **Observability** — `Hᵤ` healing-utility score, per-source resolution
  rate, drift-type breakdown, threshold alerts, persisted history in
  `uasr_healing_metrics`.

## Shipped but still unreachable — off by default, no env var

The same pattern PR #256 just fixed for numeric healing exists for three
more detectors in `mapek_worker.py`, none exposed through
`runtime_config.py`:

- **S18.1 Wasserstein-Martingale drift detector** — Azuma-Hoeffding
  false-positive bound, tested (`test_mapek_martingale_integration.py`), but
  `use_martingale_detector` has no `UASR_*` env var.
- **S18.1c Kramer-Magee `ShimRouter` canary** — traffic-splitting deploy
  replacing pause/resume, `use_shim_router` unexposed.
- **S18.1b `CausalRLEvaluator`** — off-policy shim selection by counterfactual
  expected improvement, `use_causal_rl_evaluator` unexposed.

An operator cannot turn any of these on without a code change today — they
exist, are tested, and do nothing in production.

## Explicitly out of scope (S41 boundary — still correct, don't relitigate)

- Infra outages, auth failures, and pipeline *code* bugs — fail-closed alert
  only, never auto-fixed.
- Pipeline auto-discovery — sources are registered manually
  (`POST /uasr/baseline`).

## Missing entirely for effective autonomous self-healing

1. **Schema-evolution intent** — a drift detector can't distinguish an
   intentional `ALTER TABLE ADD COLUMN` from real drift; it alerts either
   way until a human re-registers the baseline.
2. **Post-heal effectiveness tracking / auto-rollback** — a deployed shim is
   never re-checked against the drift it was meant to fix. If it makes
   things worse, nothing reverts it automatically; an operator has to
   notice and call `POST /uasr/rollback` by hand.
3. **Approval-queue escalation** — `PENDING_APPROVAL` has no timeout. An
   unattended queue item waits forever; no paging, no auto-escalation.
4. **Per-tenant repair fairness** — `UASR_REPAIR_MAX_GLOBAL_CONCURRENT` is
   fleet-wide only; one noisy tenant's drift storm can starve every other
   tenant's recovery budget.
5. **Cross-source correlation** — each source's drift is analyzed in
   isolation; no signal for "these five sources drifted together, this is
   one upstream incident," which is exactly the pattern a human data
   engineer would spot first.

## Top 3 next-sprint candidates, ranked

**1. Wire the three remaining S18.1 flags to the environment.**
Mechanically identical to PR #256: add `UASR_USE_MARTINGALE_DETECTOR`,
`UASR_USE_SHIM_ROUTER`, `UASR_USE_CAUSAL_RL_EVALUATOR` to
`runtime_config.py`, thread through `service._mapek_config()`, add each to
`deployment_summary()` and `docs/DEPLOYMENT.md`. All three detectors are
already tested — this is pure reachability, the cheapest possible win, and
should ship before anything below it.

**2. Post-deployment heal validation + auto-rollback.**
After a shim deploys, re-run drift detection on the next N batches
(`UASR_POST_HEAL_VALIDATION_BATCHES`, default off = current behavior). If
post-deploy drift hasn't improved after N batches, auto-revert
(`_deployed_shims` pop, record status `ROLLED_BACK`, alert with reason).
Closes the biggest trust gap: today a bad auto-heal is silently permanent.

**3. Approval-queue timeout + escalation.**
`UASR_APPROVAL_TIMEOUT_SECONDS` on `PENDING_APPROVAL` records; a background
reaper (same lifespan pattern as the existing MAPE-K worker task) flips
stale items to `ESCALATED` and fires an alert. Closes the "supervised mode
silently does nothing forever" failure mode that undermines S41's own
human-in-the-loop guarantee.

Candidates 4–5 (per-tenant fairness, cross-source correlation) are real gaps
but larger and more architecturally invasive — good backlog material, not a
first move.
