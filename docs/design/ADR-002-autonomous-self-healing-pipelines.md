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
| Retype (cast back) | Each value casts losslessly (round-trip equal), **and** the cast-back values pass the same distribution proof against the baseline as a unit repair (range, moments and the aligned-histogram test; the label test for a categorical column). A lossless cast proves the representation, not the values: an int column ×2.5 sent as floats casts its whole values back exactly (BUG-577). An identifier or date has no distribution to compare (BUG-573) | Rows that fail the cast are quarantined, never nulled. If the cast-back values fail the distribution proof, or a value carries a fraction where the column records whole numbers (an int count ×2.5 sends an odd count as 7.5), the change is unproven and every row carrying the column is quarantined |
| New column | Kept: the schema evolves, nothing is dropped. The destination gains the column, or it is held in an overflow field | — |
| Unit or scale (×10ⁿ) | One factor brings ≥ 99.9% of values back inside the baseline's range and moments, and no other factor does | Quarantine |
| Duplicates | The rows are exact duplicates on the declared key, or on the full row when no key is declared. A declared key (the baseline's `metadata.key_columns`) is unique by declaration: a key value repeated in a batch, or already delivered by an earlier page of the same run (`metadata.run_id`), is a duplicate every time | Report and alert only |
| Null spike, volume drop, missing or late run | Detection only. There is nothing to repair in the data | Alert and mark the run DEGRADED. A late run is re-run once |

LLM-written fixes are never applied automatically. They can be proposed in the log.

## Safety net

Every applied fix goes through these four steps.

1. **Sandbox.** The fix runs on the triggering batch, and its output must pass the same checks.
   In a batch the healer changed (a repair applied or a row held), a numeric column no rule
   repaired is also held when its values changed: more decimal places than the column records,
   rows past the baseline's range more often than chance explains (the exact past-range test,
   1e-3 for the whole batch split over the columns it tests), a difference proven at 1e-6, or a
   value past the *unit-fault reach*. The reach is a heuristic hold, not a proof: no exact test
   can prove one row past the range (sk_digits: one pixel 1.0 → 10000.0 beside a proven rename),
   so a value whose magnitude exceeds the baseline's largest by more than ten times, and by more
   than the baseline's own dynamic range (largest over smallest magnitude), is held. On clean
   batches of 5–300 rows (1,000 seeds per baseline size, one batch each) it held lognormal(0, 2)
   and (0, 2.5) columns never, and pareto(1.2) columns, an infinite-variance tail, in 0.4% of
   batches at 200 baseline rows, 2.5% at 100 and 9.5% at 50. Like every sandbox check it only
   holds rows, never returns or alters them. A batch flagged only by detection-only findings
   (duplicates, a null spike, volume) changed nothing: it comes back as sent with its alert,
   after the checks a clean batch gets and the unit-fault reach alone (a count ×1000 beside a
   null spike is a unit fault the detector could not prove, not a value to return under an
   alert), never the decimal-place, past-range or aligned holds, which at the first floor held
   79 of 650 real keyed-duplicate batches, 81 of 650 null spikes and 26 of 650 duplicates for
   values the same batch, unflagged, returns.
2. **Canary.** For the next N batches (default 3), the fixed output is checked against the
   baseline. Any regression rolls it back automatically. A regression is evidence against the
   fix: its output on the column is provably not the baseline's (the detector's exact tests),
   or not of the column's kind. A batch that only fails to prove the fix again is quarantined,
   never repaired blindly, and the watch continues: rolling back a correct mapping on such a
   batch blocked it on every later batch (steady-state seeds 194 and 336).
3. **Durable record.** Baselines, deployed fixes and watch state live on `/data` in SQLite or
   DuckDB, so they survive restarts.
4. **Signed log.** Each detection, fix, quarantine and rollback is written append-only through
   `shared/audit_ledger`, ED25519-signed, so the review afterwards can be verified.

## Measuring the accuracy

A fault-injection benchmark in CI holds clean datasets with known ground truth. For each one it
injects every fault type (rename, retype, scale, decoys, new and removed columns, nulls,
duplicates, keyed duplicates, volume, and pairs of them) many times, across seeded trials, in
three lanes: synthetic datasets (500 trials per fault type), real datasets bundled with sklearn
and statsmodels plus the COMPAS extract (50 per dataset and fault type), and steady state (500
multi-batch streams through warm-up, the canary's keep and rollback paths, and clean batches
after the fault). It measures:

- **(a) Applied-fix precision**, gated at ≥ 99.9% overall and per fault type. Every batch where
  a fault was detected and rows were returned counts, and it is correct only when every returned
  row equals its ground truth: a flagged batch that passes a changed column through is a failed
  fix. Every duplicate on a source with a declared key counts too, detected or not, and is
  correct only when the duplicates alert was raised.
- **(b) Strict outcome accuracy**, reported: correct / (correct + wrong) over every fault batch,
  silent misses included, split into wrong applied fixes, silent misses (a fault never detected,
  its data passed unchanged) and missed alerts (a detection-only fault left unreported), per
  fault type and dataset.
- **(c) Healed automatically (recall):** fault batches that came back exactly right -- repaired,
  or, for a detection-only fault, returned untouched with its alert -- out of all fault
  batches. Abstaining (quarantine) lowers it, never (a).
- **Detected** (healed, held or alerted): 1 minus the silent-miss rate -- the fault batches the
  healer did not pass through unflagged.
- **(d) False alarms** on clean data, gated at 0 in every lane: any finding, held row or change
  on a clean batch, including the clean earlier pages of a run.

**The miss floor.** The lanes are seeded, so their misses are exact numbers.
`aurabackend/tests/accuracy/accuracy_floor.json` records, per lane at the trial count CI runs,
the silent misses, the missed alerts and recall. Each lane is gated: silent misses and missed
alerts no higher than the floor, recall no lower. The floor moves only deliberately: when
detection improves, the same change lowers the miss counts and raises recall to the new run's
numbers; a miss count is never raised, nor recall lowered, without an owner's decision named in
that commit. The `heal-realism` CI job enforces it for the real and steady lanes, the
`heal_accuracy` lanes for the synthetic one.

**Keyed duplicates.** When a source declares a unique key, any repeated key value in a batch, or
a key value an earlier page of the same run delivered, must raise the duplicates alert, every
time. A page's keys join the run when its rows are delivered (a held row's key never does), and
a page re-sent unchanged is the same page, not its own duplicate. Key values compare by the key
they name: 1, 1.0 and "1" are one key; "007" and True are not 1. A run's keys live in the UASR
process's memory (at most 2,000,000 hashed keys over all sources and tenants, least recently
used runs evicted first): a restart, a page another replica serves, or an eviction starts the
run's memory afresh, and a key repeated across that gap is not caught. Every lane runs
keyed-duplicate trials (the synthetic identifier, a real source's natural key such as
`(firm, year)`, or a row key added to the rows): exact copies, rows that take another row's key,
and copies of an earlier page's rows. A missed keyed duplicate is wrong under (a) and is gated
at 0 misses. Duplicates on keyless data stay as they were: reported under (b), and held by the
floor.

**The public claim** is stated per lane, with both numbers named, and never as a bare "99.9%
accurate": "≥ 99.9% of automatic fixes correct; healed automatically N%, detected M% (by
lane)". At the floor of 2026-11-26: no wrong fix in 5,332 synthetic, 4,662 real and 776
steady-state applied batches; healed automatically 70.7% on synthetic data, 51.5% on real data
and 79.0% in steady state; detected 100%, 93.9% and 99.1%. A fault not healed was held back
(quarantined, never written) or missed: no miss on synthetic data; 550 silent misses in 8,950
real trials (448 duplicates on keyless data, 65 decoy rescales by x2.5 or x7, 25 rescales, 10
null spikes, 2 combinations) and 19 missed alerts (combinations); 9 silent misses in 977
steady-state fault batches (7 duplicates on keyless data, 2 rescales).

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

## Phase 2 contract (2026-10-29)

Order matters. The repairs that exist today include two that the accuracy rule forbids: dropping
a new column (`actuator_agent.py`) and nulling values that fail a cast. Wiring the healer into
real pipelines first would push guessed fixes into customer data. So phase 2 has two parts:

**2a (now), in parallel:**

1. **Proven repairs and quarantine** (`uasr/actuator_agent.py`, `uasr/recovery_loop.py`, the
   `/uasr/heal` response). Every repair follows the table above.
   - `/uasr/heal` gains `quarantined_rows: [{row, reason}]` and `repairs: [{type, column, proof}]`.
   - `healed` is true only if every returned row is either clean or repaired by a proven rule.
   - A repair that cannot be proven quarantines the affected rows, or the batch for
     batch-level faults. It never alters the rows.
   - Template repairs that are not provable are removed from the auto-apply path.
2. **Accuracy benchmark** (`aurabackend/tests/accuracy/`), built on 2a.1's contract.
   - Inputs: seeded clean datasets with ground truth; every fault type injected across at least
     500 trials.
   - Measured: precision of applied repairs, recall, and false alarms on clean data.
   - CI lane fails if precision is below 99.9% or any clean-data false alarm occurs.
3. **In-process MAPE-K queue** (BUG-565). UASR consumes an asyncio queue fed by `/uasr/heal` and
   `/uasr/ingest`, in place of Kafka.
   - The Kafka bootstrap runs only when `UASR_KAFKA_SERVERS` is set.
   - `/uasr/mapek/status` reports the real consumer state.
   - Post-heal validation (the canary over the next N batches, with auto-rollback) runs on this
     queue.
4. **Freshness.** A pipeline with a schedule records its last successful run. A periodic check
   marks a late or missing run, alerts through webhooks, and re-runs it once.

**2b (after 2a's benchmark passes):**
- A heal step in `pipeline/engine.py` after each source load, paged within the box's memory.
- A heal step in streaming, per micro-batch.
- Quarantined rows go to a per-pipeline quarantine table, never to the sink. The run reports
  DEGRADED.
- Whole-run batches carry `metadata.complete_run` so the volume check runs.

## Phase 2b contract (2026-11-07)

Phase 2a ended with the benchmark green: 0 WRONG of 7000 fault trials, 0 false alarms in 1000
clean batches, recall 0.68. Phase 2b connects the healer to the pipelines that actually move
data.

- **Where:** after each source load in `pipeline/engine.py` (scheduled and manual runs, ETL),
  and per micro-batch in the streaming engine, before any transform or sink sees the rows.
- **How:** the gateway calls UASR `/uasr/heal` over HTTP as the pipeline's tenant (the
  BUG-562 mechanism).
  - Rows go in pages that fit the 1 GB box. Healed rows replace the source rows.
  - The source id is `pipeline:<pipeline_id>:<source>`, scoped to the tenant.
- **First run:** the source's clean first load registers the baseline (`only_if_absent`); it
  is not healed against itself.
- **Whole runs:** the run's last call carries `metadata.complete_run` with the row count, so
  the volume check runs per run, never per page.
- **Quarantine:** quarantined rows are never written to a sink.
  - They go to a per-pipeline DuckDB table `_aura_quarantine_<pipeline_id>`, with run id,
    reason and the original row.
  - The run reports DEGRADED with counts, repairs and alerts.
- **Fail closed:** if UASR cannot be reached or answers an error, no rows from that load
  reach a sink. The run is FAILED with that reason, and the scheduler's retry and freshness
  re-run cover it. Writing unchecked data would be a guess.
- **Accuracy:** the phase-2a benchmark stays green. Pipeline tests reuse its fault injectors
  end to end.

## Detection limits (BUG-579)

A numeric column's shift is proven only by tests that hold for any distribution, at 1e-6 a
column. None of them treats the baseline's sample std as the true one.

- **Past-range test:** the chance that k batch rows all lie outside the baseline's range. It is
  exact under exchangeability, with a union bound over the low/high splits.
- **Rank test, while either sample has ≤ 64 rows:** Mann-Whitney on the baseline's bins.
  - A pair in one bin counts as no evidence. That count is at most the raw strict count, which
    is at most the rank sum after random tie-breaking, and that sum has the tie-free null
    distribution. So it stays valid with any ties, and with bin edges taken from the
    baseline's own min and max.
  - The tail is bounded from above by a Chernoff bound (Hoeffding: sampling without
    replacement), and by an exact count on a grid of score groups while the smaller sample has
    ≤ 16 rows.
- **0/1 and small-int columns, while either sample has ≤ 64 rows:** an exact rank test on the
  raw value counts, ties and all.
  - It applies to an int or bool column whose range is shorter than its 50 bins, with at most
    16 distinct values. There each bin holds one value, so the baseline's histogram is its raw
    counts, and the bin edges never enter the test.
  - The p-value is P(|W − E W| ≥ |w − E W|) under the tie-respecting permutation null. On a
    0/1 column this is Fisher's exact test.
- **Rank test, both samples > 64 rows:** the BUG-575 cut, |z| > 5.
- **Interior G-test:** only with ≥ 40 rows inside the range.
- **Infinite values:** ±inf in a column whose baseline is all finite fires on its own, like a
  change of kind. This rests on an assumption, not a distribution-free proof: a source whose
  baseline holds no inf is taken not to emit inf legitimately.
- **One bin width:** a baseline and a batch binned on its edges both use (top edge − bottom
  edge) / bins. Two formulas that differed by an ulp put a baseline 65.58 in bin 4 and a
  batch 65.58 in bin 5, which broke the rank test's tie bound.

**Rows ×1000 needed to fire.** Batch of N(100, 15) rows, k of them multiplied by 1000,
detect() firing in 5/5 trials. The analytic past-range bound is in brackets.

| Baseline rows | batch 10 | batch 30 | batch 64 | batch 300 | batch 2000 |
|---|---|---|---|---|---|
| 100 | 6 (6) | 11 (11) | 19 (17) | 63 (57) | 391 (367) |
| 1,000 | 4 (4) | 5 (5) | 7 (6) | 13 (12) | 49 (44) |
| 10,000 | 3 (3) | 3 (3) | 4 (3) | 6 (5) | 11 (9) |

At 266aefd one row fired in every cell: the >2σ mean rule fired by itself. That same rule
quarantined ~40% of clean 5-row batches.

**What small batches cannot prove**

- **A few extreme rows in a large batch:** 5 rows at 1500 in 300 rows of N(100, 10) is no
  longer caught (20/20 at 266aefd, 0/20 now). Without knowing the shape, the top 5 of the 600
  pooled values all coming from the batch has p ≈ 0.2.
- **Very small samples:** 2 rows against a 400-row baseline can reach at best 1/C(402, 2)
  ≈ 1.2e-5. Baselines under ~40 rows prove only large, nearly total shifts.
- **Tied columns:** heavy tails whose mass sits in the first bins, and int columns wider than
  the exact test's reach. Pairs in one bin are no evidence, so these need more rows than a
  continuous column. 0/1 and small-int columns take the exact test instead. A 15% flag
  flipped to 85% is caught exactly as often as Fisher's exact test proves it at 1e-6: 9/20
  at 10 rows against 100 baseline rows, 20/20 from 20 rows (0/20 at fea7658).

**Calibration**

- **Exact 0/1 and small-int test:** exact by construction.
  - On a clean 0/1 column, summed exactly over the binomial counts (rates 2-50%, baselines
    2-1000 rows, batches 10-300 rows), detect() fires on at most 6.8e-7 of batches, apart
    from the interior G-test cell below.
  - On digits, Poisson(1), Poisson(3), 15% flag and zero-inflated columns (20,000 trials per
    cell; baselines 5-1000 rows, batches 10-64 rows), the rate under 1e-1..1e-4 is at or under
    the level. The one exception is conditional: when a 5-row zero-inflated baseline happens not
    to be constant, the rate runs up to 1.4× nominal. A constant baseline never takes the
    test, so over all batches the rate stays at the level.
  - Being exact, it fires at its level rather than far under it. In 20,000 clean six-column
    small-int batches it raised one alarm: a uniform-digit column whose 21 rows held 12
    zeros (chance 1.2e-7, exact p 5e-7). The level allows up to ~0.12 such alarms in
    120,000 column checks.
- **Rank test:** conservative by construction. It used to condition on the pooled bins, and
  because the bins are cut on the baseline's own range it ran 1.0-1.8× nominal on tiny
  baselines. In 20,000 trials per cell (uniform, 15% boolean and Poisson(1) columns; baselines
  of 2-39 rows; batches of 12-1000 rows), the rate under 1e-2, 1e-3 and 1e-4 never exceeded
  the level beyond Monte Carlo noise. On the boolean and count columns it stayed far under it.
- **Interior G-test:** a chi-square approximation. On a clean 15% boolean column with a
  100-row baseline it fires on 1.9e-6 of 300-row batches and 3.3e-6 of 1000-row batches,
  summed exactly over the binomial counts (300-row batches: 1.8e-5 at 266aefd). Of the
  0/1-column cells checked (rates 2-50%; baselines of 2-1000 rows against batches of 10-300,
  and 2-100 against 65-1000), these two are the only ones over the nominal 1e-6.
