# S31b — Audit Engine & Demo Data — Design Spec

**Date:** 2026-05-29
**Track:** S31b (YC demo — backend) · branch `feature/s31b-audit-engine`
**Owner:** Mounith
**Pairs with:** S31a (frontend — Rohith): Audit Submit wizard, live progress, Audit Certificate page, public `/verify/{hash}` page.

> Sprint-number note: the labels `S31a`/`S31b` are already used in the repo for
> earlier test-coverage work (e.g. `test_code_generation.py` "Sprint S31b").
> We keep the branch name `feature/s31b-audit-engine` from Rohith's coordination
> doc, but commit subjects will say "S31b (YC demo)" to avoid registry confusion.

---

## 1. Goal

Deliver a **one-click, investor-legible, cryptographically-verifiable compliance
audit** for the YC "AI-Native Service Companies" submission. It must look and
behave like a *service*, not a tool: pick a regulated scenario → watch the audit
run → get a signed certificate anyone can independently verify.

This is **assembly, not green-field**: AURA already has the counterfactual
estimation engine, ED25519 signing, RFC-6962 Merkle inclusion proofs, artifact
persistence/replay, and PDF rendering. S31b packages those into a demo.

## 2. What already exists (build on, don't rebuild)

| Capability | Where | Status for S31b |
|---|---|---|
| Counterfactual fan-out: 6 estimators (`linear_regression, ipw, psm, double_ml, forest_dr, tmle`) × 4 refuters × adversarial critic | `counterfactual_service/engine.py` (`run_job`, `_run_one_estimator`) | Reuse. Add IV as 7th. |
| ED25519 signing, env-hex / env-PEM / ephemeral key sources | `counterfactual_service/signing.py` | Add 4th source: auto-generate + persist. |
| Artifact persistence, replay, `/verify`, public-key, Merkle STH + inclusion proof | `counterfactual_service/main.py`, `persistence.py`, `shared/audit_log.py`, `shared/merkle.py` | Reuse as-is. |
| PDF report + `GET …/report.pdf` (501 if reportlab absent) | `counterfactual_service/pdf_renderer.py` | Polish. |
| Pre-register an in-memory DataFrame under a `source_id` | `main.py::register_dataset` | Reuse — this is the demo-data hook. |
| Gateway proxy to counterfactual service | `api_gateway/routers/counterfactual.py` | Extend with `/demo` passthrough. |

## 3. Architecture — new units

Each unit has one purpose, a clear interface, and is testable in isolation.

### 3.1 Scenario registry — `counterfactual_service/demo_scenarios/`

```python
# demo_scenarios/base.py
class DemoScenario(Protocol):
    id: str                 # "fair_lending"
    title: str              # "Fair-Lending Credit Decision Audit"
    vertical: str           # "compliance"
    description: str        # one-sentence pitch line
    instrument: str | None  # column name for IV, or None
    def build_dataset(self) -> pd.DataFrame: ...
    def query(self) -> CounterfactualQuery: ...
    def narrative(self, artifact: dict) -> str: ...  # plain-English conclusion for PDF/UI

SCENARIOS: dict[str, DemoScenario]  # id -> scenario
```

Scenarios #1–4 are independent implementations. Shipping 2-of-4 still yields a
clean demo: the `/demo/scenarios` list only advertises what's registered.

### 3.2 Deterministic synthetic data

Each scenario builds its dataset with **seeded NumPy** (byte-stable, no PII, no
licensing). Crucially we **plant a known ground-truth effect**, so the audit
*proves the engine recovered the disparate impact we baked in* — a stronger
investor moment than numbers on random data, and reproducible for stable
signatures.

### 3.3 Persistent signing key — `signing.py` 4th source

When neither `AURA_SIGNING_PRIVATE_KEY_HEX` nor `_PATH` is set, instead of an
ephemeral key:
1. Look for `AURA_SIGNING_KEY_DIR/signing_ed25519.pem` (default dir
   `data/keys/`).
2. If present → load it (`key_source="persisted_file"`).
3. If absent → generate, write PEM at `0600`, then load.

Effect: certificate `/verify` badge stays valid across restarts **with zero env
config**. Ephemeral remains the final fallback if the dir is unwritable
(fail-soft, unchanged posture).

### 3.4 IV estimator — 7th slot

New `method_key == "iv"` branch in `_run_one_estimator`, **opt-in** like
`tmle`/`forest_dr`. Uses DoWhy's `iv.instrumental_variable` (2SLS numeric
fallback if dowhy-iv unavailable). Requires the DAG to declare an instrument;
absent dep or instrument → structured `CounterfactualEstimate(error=...)`, never
a crash. `fair_lending` declares a valid instrument so IV is genuinely
demonstrable. Update `info()` estimator list (currently stale: lists only 4).

### 3.5 `/demo` endpoints (counterfactual_service) + gateway proxy

- `GET  /counterfactual/demo/scenarios` → `[{id, title, vertical, description}]`
- `POST /counterfactual/demo/{scenario_id}` → registers the scenario dataset via
  `register_dataset`, submits a **real async job** (`run_job` fan-out), returns
  `{job_id, scenario_id}`.
- Frontend polls existing `GET /counterfactual/jobs/{job_id}` for live progress,
  then reads the artifact (hash, `signature_status`, `signing_key_source`) and
  links `GET /counterfactual/artifacts/{hash}/verify` + `…/report.pdf`.
- Gateway: add passthrough routes under `api_gateway/routers/counterfactual.py`.

### 3.6 Startup pre-warm + fail-safe

A lifespan hook runs each registered scenario's audit once at startup and caches
the sealed artifact keyed by `scenario_id`. Behaviour:
- First `POST /demo/{id}` can return the pre-warmed `job_id`/artifact instantly.
- Datasets are small (~few-hundred rows) so a fresh live run still finishes in
  ~1–3 s — preserving the "watch it compute" progress moment.
- **Fail-safe:** if a live run errors, return the last good cached artifact with
  `degraded: true` so the demo never shows a broken state.

### 3.7 PDF polish — `pdf_renderer.py`

Add: scenario/vertical header, plain-English disparate-impact conclusion
(from `scenario.narrative`), hash + ED25519 + `/verify` attestation block,
estimator-agreement summary line.

## 4. Scenario #1 — `fair_lending` (the MVP scenario)

- **Question:** "Did the applicant's protected attribute *cause* the denial,
  holding creditworthiness fixed?"
- **Treatment:** `protected_class` (binary).
- **Outcome:** `approved` (binary).
- **Confounders:** `income`, `dti` (debt-to-income), `credit_score`,
  `loan_amount`, `employment_years`.
- **Instrument (for IV):** `officer_assignment` — quasi-random loan-officer
  assignment that shifts approval propensity (officers vary in leniency) but does
  not affect the applicant's underlying creditworthiness. Satisfies the IV
  exclusion restriction by construction.
- **Planted ground truth:** a modest direct effect of `protected_class` on
  `approved` after adjustment (the "disparate impact" the audit must recover),
  plus confounding so the naive difference overstates it — demonstrating *why
  causal* adjustment matters vs a raw rate gap.

## 5. Contract for S31a (frozen JSON shapes)

```
GET  /counterfactual/demo/scenarios
  -> { "scenarios": [ {id, title, vertical, description} ] }

POST /counterfactual/demo/{scenario_id}
  -> { "job_id": "ca_…", "scenario_id": "fair_lending", "degraded": false }

GET  /counterfactual/jobs/{job_id}
  -> { job_id, state: queued|running|succeeded|failed, artifact, error }
     # artifact includes: audit_record_hash, estimates[], refutations[],
     #   signature_status, signing_key_source, rendered

GET  /counterfactual/artifacts/{hash}/verify
  -> { record_hash, verified: bool, signature_status, signing_key_source, reason }

GET  /counterfactual/artifacts/{hash}/report.pdf  -> application/pdf
GET  /counterfactual/public-key                   -> { public_key_pem, key_source }
```

These are the integration boundary; S31a builds the wizard/progress/certificate/
`/verify` pages against them. No file overlap with S31b.

## 6. Testing (Tier A + Tier B)

**Tier A (always-on lane, pure Python):**
- Registry: each scenario yields a valid non-empty df + a `CounterfactualQuery`
  whose treatment/outcome/confounders are columns of the df; `build_dataset` is
  deterministic (two builds byte-identical).
- Persistent key: generate-then-reload returns the *same* public key; honours an
  existing PEM; falls back to ephemeral when dir unwritable.
- `/demo`: `scenarios` lists registered ids; `POST` returns a job_id; fail-safe
  returns cached artifact with `degraded: true` on a forced live error.
- IV dispatch: returns a structured `error` estimate (no crash) when dep/
  instrument absent.

**Tier B (gated lane: dowhy + econml):**
- IV estimator numeric correctness on a known-instrument DGP.
- Full `/demo/fair_lending` audit recovers the planted effect within the
  reported CI.

## 7. Build order (fail-safe — ship whatever's done)

1. **MVP:** registry + `fair_lending` + `/demo` endpoints + gateway proxy +
   startup pre-warm. → working one-click audit.
2. Persistent signing key. → certificate is real/stable.
3. IV estimator (7th slot) on `fair_lending`. → analytic-depth deliverable.
4. PDF polish.
5. Scenario #2 `insurance_underwriting` → #3 `healthcare_prior_auth` → #4
   `hiring_fairness`.

Each step is independently mergeable and leaves the demo working.

## 8. Risks / mitigations

- **IV external dep** (dowhy-iv / econml): isolated behind opt-in dispatch +
  structured error; if it slips, cut from MVP (design item 7.3 is deferrable
  without touching 1–2).
- **Live audit latency on a laptop demo:** mitigated by small datasets +
  startup pre-warm + cached-artifact fail-safe.
- **Synthetic-data credibility:** mitigated by planting a *known* effect and
  showing the engine recovers it — and by the naive-vs-causal gap making the
  point that adjustment matters.

## 9. Non-goals (YAGNI)

- No real customer data ingestion for the demo (synthetic only).
- No new auth/multitenancy for `/demo`.
- No streaming/websocket progress (frontend polls existing `/jobs/{id}`).
- No persistence-layer schema changes (reuse artifact persistence as-is).
