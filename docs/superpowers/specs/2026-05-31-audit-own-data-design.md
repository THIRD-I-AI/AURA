# Audit Your Own Data — Design Spec

**Date:** 2026-05-31
**Branch:** `feature/audit-own-data`
**Owner:** Mounith
**Motivation:** `ARCHITECTURE_AUDIT.md` — turn the synthetic-only demo into a tool a
real person can run on *their own* decision data. This is the line between
"impressive demo" and "a product someone uses."

---

## 1. Goal

A user uploads their own decision data (loan approvals, claims, hires, …), maps
their own columns, and gets a **signed, causally-honest certificate**: the
estimated effect, an explicit identification assumption, and a sensitivity bound
— computed **out-of-process** so a real audit never freezes the gateway.

## 2. What already exists (build on, don't rebuild)

| Capability | Where | Status |
|---|---|---|
| Upload a CSV | `api_gateway/routers/files.py` `POST /upload` → `data/uploads/` | Reuse |
| Resolve an uploaded file as a DataFrame | `counterfactual_service/main.py::_resolve_dataset` (handles `uploaded_file:` source IDs, line 116) | Reuse |
| Run an arbitrary `CounterfactualQuery` | `submit_job(query)` → `run_job` | Reuse, but it offloads NOTHING (freezes) and defaults to slow estimators |
| Column introspection | `shared/data_utils.py::smart_load_csv` (returns `columns`, types, `headers_inferred`) | Reuse |
| **E-value + robustness on every estimate** | `schemas.py::SensitivityReport` (already computed in the fan-out) | **Reuse — this is the honesty layer's foundation** |
| Custom-audit wizard (treatment/outcome/confounders) | Rohith's `frontend/src/audit/AuditWizard.tsx` | Extend the contract (file-picker + instrument); frontend is Rohith's |
| Signed cert + `/verify` + PDF | demo flow | Reuse unchanged |

So this is **complete + harden + make-honest** the existing custom-audit path, not
green-field.

## 3. Locked decisions

- **Identification strategy:** *backdoor-by-default + mandatory sensitivity, IV
  opt-in.* On real observational data you cannot verify "no unmeasured
  confounders," so AURA **discloses it as an explicit assumption** and quantifies
  fragility with the already-computed **E-value**. Default estimators are the fast
  modern backdoor methods `["double_ml", "tmle"]`; IV (`"iv"`) is added **only when
  the user maps an instrument**, with a first-stage strength check.
- **Worker boundary:** *`ProcessPoolExecutor` offload.* Threads are ruled out — we
  empirically observed GIL-bound dowhy starving the event loop even off-thread.
  Each audit runs in a child OS process; the gateway loop stays responsive. Same
  `/audit → job_id → poll` interface a Redis queue would expose, so a later
  upgrade to a durable worker is an internals swap, not an API change.

## 4. Architecture — new/changed units

### 4.1 `build_dag_from_mapping(treatment, outcome, confounders, instrument=None) -> DAGSpec`
Pure function. Canonical backdoor DAG, valid by construction:
- each `confounder → treatment` and `confounder → outcome`
- `treatment → outcome`
- `instrument → treatment` (only if `instrument` given)
New module: `counterfactual_service/audit_mapping.py`.

### 4.2 `validate_and_prepare(df, mapping) -> (clean_df, DataQuality)`
Boundary hygiene on real, messy CSVs:
- all mapped columns exist (else `400` with the missing names)
- coerce treatment/outcome/confounders/instrument to numeric; non-coercible →
  recorded warning
- drop rows with missing values in mapped columns; **count** them
- enforce `n_clean >= AUDIT_MIN_ROWS` (default 100) else `400`
- flag a non-binary treatment (the estimators binarise on `actual`/`counterfactual`)
- returns `DataQuality{n_input, n_clean, n_dropped, warnings[], treatment_is_binary}`
Lives in `audit_mapping.py`.

### 4.3 Estimator selection
`methods = ["double_ml", "tmle"] + (["iv"] if instrument else [])`. Never the slow
DoWhy bootstrap estimators (`linear_regression/ipw/psm`).

### 4.4 Out-of-process execution
`counterfactual_service/audit_worker.py`:
- module-level `ProcessPoolExecutor(max_workers=AUDIT_POOL_WORKERS, default 2)`
- top-level picklable `run_audit_subprocess(payload: dict) -> dict`: resolve file →
  `validate_and_prepare` → `build_dag_from_mapping` → `CounterfactualQuery` →
  `asyncio.run(run_job(query, df, methods))` → returns artifact dict + DataQuality +
  identification statement.
- gateway side: inside the async job task, `await loop.run_in_executor(_POOL,
  run_audit_subprocess, payload)`. GIL-bound work is in the child; the loop is free.
- job state in the existing `_jobs` dict (in-memory — durability is a separate
  roadmap item, explicitly out of scope here).

### 4.5 Honesty layer (the differentiator)
The artifact returned for a user audit carries, beyond the estimates:
- **`identification`**: plain-English statement, e.g.
  *"Estimate assumes no unmeasured confounding beyond: income, dti, credit_score.
  Strengthen by adding confounders or an instrument."* For IV, append the
  exclusion-restriction caveat + first-stage strength.
- **`sensitivity_headline`**: from the existing `SensitivityReport` —
  *"A hidden confounder would need an E-value of {e} (vs. measured associations) to
  fully explain away this effect."* Surfaced prominently, not buried.
- **`data_quality`**: the `DataQuality` report (n, dropped, warnings).
These are added to the returned **job-result dict** *after* `run_job` has sealed +
signed the `CounterfactualArtifact` (the same mechanism the demo used for
`narrative`/`degraded`/`cached`) — **not** into the hashed Pydantic artifact. So the
signed hash basis and `/verify` are unchanged; the certificate + PDF just render the
extra fields.

## 5. API contract (for Rohith's wizard)

```
POST /counterfactual/audit
  body: { uploaded_file: str, treatment: str, outcome: str,
          confounders: string[], instrument?: str }
  -> { job_id }                          # instant; pool-offloaded
  -> 400 { detail }                      # CHEAP gateway checks only: file missing,
                                         #   or a mapped column not in the file header
  # Deeper problems found during cleaning (too few usable rows after dropping NaNs,
  # constant/degenerate treatment) surface as a FAILED job (state="failed") with a
  # clear reason — not a POST 400 — since they require reading/cleaning the data in
  # the child process.

GET /counterfactual/jobs/{job_id}        # existing
  -> { job_id, state, artifact, error }
     # artifact additionally carries: identification, sensitivity_headline, data_quality

GET /counterfactual/artifacts/{hash}/verify   # existing, unchanged
GET /counterfactual/artifacts/{hash}/report.pdf
```
Gateway proxy passthrough added under `api_gateway/routers/counterfactual.py`
(mirrors `/demo`). **Coordination note for Rohith:** his `AuditWizard` needs a
*dataset/file picker* and an optional *instrument* field to post this body.

## 6. Data flow

```
wizard → POST /api/v1/counterfactual/audit {file, treatment, outcome, confounders, instrument?}
  gateway: cheap pre-validate (file + columns exist) → ProcessPool.submit → {job_id} instant
  child process: resolve file → validate_and_prepare → build_dag_from_mapping
       → CounterfactualQuery → run_job(methods=[double_ml,tmle(,iv)])
       → signed artifact + identification + sensitivity_headline + data_quality
  poll GET /jobs/{id} → certificate → /verify + PDF (existing)
```

## 7. Testing (Tier A + Tier B)

**Tier A (always-on, pure Python):**
- `build_dag_from_mapping`: confounder edges, treatment→outcome, instrument edge
  only when given; no self-loops.
- `validate_and_prepare`: missing column → error; rows with NaN dropped + counted;
  `n < MIN_ROWS` → error; non-binary treatment flagged; type coercion.
- estimator selection: `iv` present iff instrument mapped.
- identification-statement text reflects the mapped confounders + IV caveat.
- endpoint wiring: pre-validate 400s (missing file/cols); successful POST returns a
  `job_id`; gateway proxy reachable.

**Tier B (econml + dowhy lane):**
- end-to-end: a real-ish uploaded CSV with a planted effect → audit recovers it;
  `data_quality`/`identification`/`sensitivity_headline` populated; artifact signed +
  hash present.
- IV path when an instrument column is provided.

## 8. Non-goals (YAGNI)
- No frontend (Rohith's wizard; we provide the contract).
- No durable job queue / Redis (ProcessPool now; swappable behind the same API).
- No multi-table joins or SQL — single uploaded table.
- No automatic confounder discovery — the user specifies confounders; that explicit
  choice is what makes the audit auditable.
- No change to the demo scenarios or the signed-hash basis.

## 9. Risks / mitigations
- **ProcessPool + Windows/dev spawn cost** → small pool (2), reused; jobs are
  seconds, not ms. Acceptable.
- **Pickling the payload** → pass primitives (file path + column names), not a
  DataFrame; the child resolves + cleans. No large-object pickling.
- **Garbage real data** (all-NaN, constant treatment, n too small) → caught in
  `validate_and_prepare` with a clear 400, never a crash mid-audit.
- **User maps a bad instrument** → IV runs but the first-stage strength check +
  identification caveat surface the weakness rather than silently trusting it.
