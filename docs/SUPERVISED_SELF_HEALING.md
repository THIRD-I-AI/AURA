# Supervised Self-Healing — Risk-Tiered Human-in-the-Loop Shim Approval

> **Status:** design / proposed (sprint S41). Not yet implemented.
> **Goal in one line:** turn UASR from *auto-deploy-only* into a **supervised
> first responder** — it detects, diagnoses, generates, and sandbox-validates
> a fix, then holds anything riskier than a proven template fix in an
> **approval queue** for a human to approve or reject, with full evidence.
> Fail-closed until a human (or the risk policy) says go.

## Why this, why now

The current industry pattern for self-healing data/ML pipelines is **not**
"remove the human" — it is **risk-tiered human-in-the-loop**: automate the
routine low-risk fixes, route the rest to a person to approve/edit/reject,
log every approval immutably, roll out via canary, and learn from the
decision. (See: self-healing MLOps that pages itself; HITL in data
engineering; JFrog MLOps best practices — 2025–2026.)

This maps almost one-to-one onto pieces AURA already has:

| Need | Already in the repo |
|------|--------------------|
| Generate a fix | `uasr/actuator_agent.py` (template → LLM → fallback) |
| Validate before deploy | sandbox validation in `uasr/recovery_loop.py` (deploys only if D_KL drops) |
| Hold instead of deploy | `RecoveryLoopConfig.auto_deploy=False` path (`recovery_loop.py:186`) |
| Canary rollout + rollback | `uasr/shim_router.py`, `POST /uasr/rollback` |
| Signed, immutable decision records | `shared/signing.py` + the S35 `HumanOverrideRecord` pattern |
| A human review surface | the S35 Exception Queue (`ExceptionQueue.tsx`) |

So this is an **integration of existing parts plus a small state machine**,
not a from-scratch build.

## The risk-tiering policy (decided: risk-tiered)

A generated, sandbox-validated shim **auto-deploys with no human** only when
**all** of these hold:

1. `generation_method == "template"` — a deterministic transform (column
   rename-back, type coercion, unit rescale), not LLM-authored.
2. `severity ∈ {LOW, MEDIUM}`.
3. `validation_passed` **and** `post_kl_divergence` back under the nominal
   threshold.
4. It is **not** a schema drift that drops > 50% of columns (the existing
   CRITICAL rule).

Anything else — LLM- or fallback-generated shims, `HIGH`/`CRITICAL`
severity, or large schema loss — becomes `PENDING_APPROVAL` and **waits for a
human**. The consumer stays paused and fail-closed in the meantime: no
drifted data reaches the lake until the fix is approved.

A per-source **mode** overrides the policy when you want it:
`auto` (policy as above) · `supervised` (everything waits) ·
`monitor_only` (never deploy; only detect + recommend).

## Code changes (grounded in current files)

**1. `uasr/models.py`**
- `RecoveryStatus` += `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `ESCALATED`.
- `ShimResult` += `generation_method: Literal["template","llm","fallback"]`
  (the actuator already knows which path it took — just record it).
- New `RecoveryMode(str, Enum)`: `AUTO | SUPERVISED | MONITOR_ONLY`.

**2. `uasr/actuator_agent.py`**
- Stamp `generation_method` onto the `ShimResult` (template / llm / fallback)
  so the risk gate can key on it.

**3. `uasr/recovery_loop.py`**
- Replace the bare `if self._config.auto_deploy:` gate with a
  `_should_auto_deploy(shim, drift_result)` decision implementing the policy
  above. When it returns `False` → set `PENDING_APPROVAL`, persist, raise an
  alert, and **do not deploy** (consumer stays paused).
- Add `approve(recovery_id, approver, note)` → deploy via `ShimRouter`,
  status `DEPLOYED`. Add `reject(recovery_id, approver, reason)` → status
  `REJECTED`/`ESCALATED`, alert, stay paused.

**4. `uasr/service.py`** (new endpoints)
- `GET  /uasr/recovery/pending` — the approval queue.
- `POST /uasr/recovery/{id}/approve` — `{approver, note}` → deploy.
- `POST /uasr/recovery/{id}/reject`  — `{approver, reason}` → escalate.
- `POST /uasr/sources/{id}/mode`     — set `auto|supervised|monitor_only`.
- Every decision is written as a **signed, immutable** record (reuse
  `shared/signing.py`, mirroring the S35 `HumanOverrideRecord`).

**5. Frontend — "Healing Queue" tab** (reuse the Exception Queue pattern)
- New `/app/healing-queue` tab under **Monitoring**. Each pending item shows:
  source · which of the four watch points fired · drift evidence (KL
  before/after, affected columns, old→new types) · the **actual generated
  shim code** (read-only) · sandbox validation result · **Approve / Reject**
  with a note. Approve → deploy; Reject → escalate.

## Build sequence (TDD, one small step at a time)

1. Models: `RecoveryMode`, new statuses, `generation_method` (+ unit tests).
2. Actuator records `generation_method` (+ test).
3. Recovery-loop risk gate + `PENDING_APPROVAL` path + `approve`/`reject`
   (+ tests: template+low→auto; llm-or-high→pending; approve→deployed;
   reject→escalated; fail-closed while pending).
4. Service endpoints + signed decision records (+ contract tests).
5. Frontend Healing Queue tab (reuse `ExceptionQueue.tsx`).
6. Docs + one INVESTOR_DEMO line.

## Human-in-the-loop guarantees

- **Fail-closed:** anything not auto-approved holds; no drifted data lands
  until a human acts.
- **Auditable:** every human decision is signed + immutable.
- **Reversible:** approved shims still deploy via the canary router and stay
  rollback-able.
- **Learns:** an approved healed batch becomes the new baseline (existing
  behavior), so the same drift doesn't re-fire.

## Out of scope for v1 (honest boundaries)

- Does **not** fix infrastructure outages, auth failures, or arbitrary
  pipeline *code* bugs — those remain fail-closed alerts, not auto-fixes.
- **Not** auto-discovery of external pipelines. "Attaching" a pipeline means
  registering a baseline (`POST /uasr/baseline`) and routing its batches to
  `POST /uasr/ingest` or a Kafka topic UASR consumes.

---

This lands as **sprint S41** — GitHub issue + `feature/s41-supervised-self-healing`
branch + PR, per `CLAUDE.md`.
