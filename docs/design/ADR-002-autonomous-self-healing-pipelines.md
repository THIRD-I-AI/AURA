# ADR-002: Fully autonomous self-healing pipelines

- **Status:** accepted, 2026-10-29 (owner decision). Phase 1 in progress.
- **Evidence:** [SELF_HEALING_READINESS_2026-10-29.md](SELF_HEALING_READINESS_2026-10-29.md). Today no
  AURA pipeline heals itself in production.

## Decisions

1. **No human gate.** Detection, repair, verification and rollback all run without approval.
   People verify afterwards, from a signed log of every automatic action.
2. **Accuracy target: 99.9–100% of applied fixes are correct.** The way to get there:
   - **Never guess.** A repair is applied only when it is proven on the data. Anything that
     cannot be proven is quarantined: rows are held aside with the reason, the run reports
     DEGRADED, and an alert goes out.
   - **What counts as correct** is the data the destination would have received with no fault.
   - **What it does to recall.** Abstaining lowers how many faults heal on their own, never how
     many fixes are wrong. Recall is tracked separately and improved by adding provable repair
     types.
3. **Trigger:** an in-process queue fed by pipeline runs, plus a scheduled freshness check. No
   Kafka. The 1 GB box cannot host a broker, and today's Kafka worker never runs.

## What "proven" means for each repair

| Repair | Applied only when | Otherwise |
|---|---|---|
| Column rename | The new name matches exactly one removed column, by normalised name or by value fingerprint (≥ 99% of values identical in type and distribution to the baseline column), and no other candidate comes close | Quarantine the batch |
| Retype (cast back) | **Every** value in the column casts losslessly (round-trip equal) | Rows that fail the cast are quarantined, never nulled |
| New column | Kept: the schema evolves, nothing is dropped. The destination gains the column, or it is held in an overflow field | — |
| Unit or scale (×10ⁿ) | One factor brings ≥ 99.9% of values back inside the baseline's range and moments, and no other factor does | Quarantine |
| Duplicates | The rows are exact duplicates on the declared key, or on the full row when no key is declared | Report and alert only |
| Null spike, volume drop, missing or late run | Detection only. There is nothing to repair in the data | Alert and mark the run DEGRADED. A late run is re-run once |

LLM-written fixes are never applied automatically. They can be proposed in the log.

## Safety net

Every applied fix goes through these four steps.

1. **Sandbox.** The fix runs on the triggering batch, and its output must pass the same checks.
2. **Canary.** For the next N batches (default 3), the fixed output is checked against the
   baseline. Any regression rolls it back automatically.
3. **Durable record.** Baselines, deployed fixes and watch state live on `/data` in SQLite or
   DuckDB, so they survive restarts.
4. **Signed log.** Each detection, fix, quarantine and rollback is written append-only through
   `shared/audit_ledger`, ED25519-signed, so the review afterwards can be verified.

## Measuring the accuracy

A fault-injection benchmark in CI holds clean datasets with known ground truth. For each one it
injects every fault type (rename, retype, scale, new column, nulls, duplicates, volume) many
times, across random seeds. It measures:

- **Precision:** applied fixes whose output equals ground truth. Must be ≥ 99.9%, or the build
  fails.
- **Recall:** faults healed rather than quarantined. Reported, not gated.
- **False alarms** on clean data. Must be 0 on the reference set.

## Phases

1. **Foundations** (BUG-561..569):
   - Correct drift detection: bins aligned to the baseline, no reset of the baseline on every
     request, durable state.
   - Null, volume, duplicate and freshness checks.
   - All drift types reported, not just the first.
   - The drift webhook fires; MonitorAgent authenticates.
   - Streaming errors are surfaced.
   - An honest live check.
2. **Wiring:**
   - A heal step in pipeline runs, ETL and streaming (per micro-batch), with quarantine.
   - The in-process MAPE-K queue and the freshness schedule.
3. **Safety and accuracy:**
   - The proven-repair rules above.
   - Canary with auto-rollback; the signed action log.
   - The accuracy benchmark gating CI.
4. **Autonomy and proof:**
   - Remove the approval gate for proven fixes.
   - The 8-step end-to-end proof from the readiness report, added to
     `scripts/verify_live_deployment.py` against production.
