# Counterfactual Audit Engine — Design

**Status:** Approved · 2026-05-02 · Sprint 8 entry
**Authors:** Mounith Reddy + Claude (Opus 4.7, explanatory mode)
**Bundle target:** Sprint 8 single-bundle commit on `main`

---

## 1. Problem statement

Every NL-to-SQL analyst tool on the market today returns descriptive answers
("here's what happened") with no causal grounding and no provenance. The
problem nobody is solving end-to-end is:

> **Given a counterfactual question about historical data, return a
> causally-grounded interventional estimate, with a hash-sealed audit
> trail and an adversarial agent's challenges to the conclusion.**

The unmet pieces in the market today:

| Piece | Status in the market |
|---|---|
| Counterfactual estimation from observational data | Research libraries (DoWhy, EconML); not productised as a chat answer |
| Hash-chained audit trail of every numeric AI claim | Limited (audit logs exist; signed reproducible chain per artifact does not) |
| Adversarial-by-default analysis | Academic; nobody ships it as a default switch on a paid product |
| Single artifact for operator + auditor + analyst audiences | Tools fragment per audience; no shared reproducible artifact |

AURA already ships every primitive needed: `causal_service` (DoWhy),
`shared/audit_log.py` (hash-chained TRAIGA log), `shared/bavt.py` (budget-aware
node skipping), `agents/specialists/dar_research_agent.py` (score mode for
adversarial work), MCP server (data plane). The wedge is the **integration**,
not net-new research.

## 2. Product framing

**Name (working):** Counterfactual Audit Engine.

**The single artifact** every audience consumes:

```python
class CounterfactualArtifact(BaseModel):
    record_id: str                       # ca_<uuid>
    query: CounterfactualQuery
    estimates: List[CounterfactualEstimate]   # 3-5 methods, sorted by name
    refutations: List[RefutationResult]       # 4 standard refuters, sorted
    challenges: List[AdversarialChallenge]    # critic output, sorted
    confidence: Literal["low", "medium", "high"]
    schema_version: str
    dataset_fingerprint: str                  # sha256 of source bytes
    audit_record_hash: str                    # sealed in TRAIGA log
    rendered: dict                            # operator | auditor | analyst
```

**Three audiences over the same artifact:**

| Tier | Audience | Surface | Lands in |
|---|---|---|---|
| 1 | Operator / PM | Chat → Counterfactual Card UI; trust signal foreground | Sprint 8 |
| 2 | Board / regulator / auditor | Tier-1 + signed PDF + replay endpoint | Sprint 9 |
| 3 | Analyst / data scientist | Tier-1 + Python SDK + Jupyter rich-repr | Sprint 10 |

The engine is **identical across tiers**. Only the renderer dispatch changes.

**Out of scope for v1 (numbered so we can revisit):**

1. Causal-DAG editor UI (DAG is user-supplied JSON in v1).
2. Categorical outcomes (numeric-only in v1; EconML DRLearner in Sprint 11+).
3. Auto-discovery of treatments from arbitrary text.
4. Synchronous request/response (job pattern only).
5. Real-time streaming of partial estimates (final artifact only).

## 3. Architecture

**Decision: Standalone microservice (`counterfactual_service`, suggested port 8012).**

Mirrors `causal_service` (8010) and `dar_service` (8011). API gateway proxies
job submission and SSE progress. Heavy DoWhy / EconML deps stay isolated in
`requirements-causal.txt`.

Rejected alternatives:

* **Extend chat router with `counterfactual: true` flag.** 60s chat SLA conflicts
  with refutation latency (placebo + random-cause + subset can take 30s-2min);
  chat UI was not designed for this artifact shape.
* **Pure orchestration extension in `agents/langgraph_orchestrator.py`.** That
  file is already 280 lines and serves the chat happy-path. Mixing two job
  shapes there hurts readability.

### 3.1 Service surface (v1)

```
POST /counterfactual/jobs                              → {"job_id": "ca_<uuid>"}
GET  /counterfactual/jobs/{job_id}                     → {"status": "...", "artifact": {...}|null}
GET  /counterfactual/jobs/{job_id}/events  (SSE)       → progress events
GET  /counterfactual/artifacts/{record_hash}           → CounterfactualArtifact (deterministic replay)
GET  /counterfactual/artifacts/{record_hash}/report.pdf → tier-2 deliverable (Sprint 9)
GET  /counterfactual/info                              → engine availability + version
```

### 3.2 Job submission

The engine accepts **both** NL and structured input. The chat router may
parse NL upstream and submit a structured `treatment` / `outcome` block, or
it may submit `{"question": "..."}` and let the engine's `parse` node fill
in the rest. Both paths produce identical artifacts when the parser
agrees. Recommended: chat router parses for the operator UI (so the user
sees the parsed query before the slow engine runs); MCP clients can
submit raw NL.

Example structured submission:

```json
{
  "question": "What would Q3 revenue have been if we hadn't raised prices in May?",
  "treatment": {
    "column": "price_change_may",
    "actual": 0.08,
    "counterfactual": 0.0
  },
  "outcome": {
    "column": "monthly_revenue",
    "agg": "sum",
    "window": ["2025-07-01", "2025-09-30"]
  },
  "dag": {
    "edges": [
      ["seasonality", "monthly_revenue"],
      ["price_change_may", "monthly_revenue"],
      ["seasonality", "price_change_may"]
    ]
  },
  "dataset": { "source_id": "uploaded_file:sales_2025.csv" },
  "audience": "operator"
}
```

`audience` only changes the **renderer**. Identical `audit_record_hash` for identical
inputs across audiences.

## 4. Engine internals

### 4.1 LangGraph sub-DAG

```
START
 ├─ parse_or_passthrough  ← CounterfactualParserAgent (NL → spec) | passthrough if structured
 ├─ resolve_dataset       ← schema_version (alembic rev) + dataset_fingerprint (sha256)
 ├─ identify              ← causal_service.identify(DAG, query)  (DoWhy identify_effect)
 ├─ estimate_fanout       ← asyncio.gather over [linear_regression, IPW, PSM, double_ML]
 ├─ refute_fanout         ← asyncio.gather over [random_common_cause, placebo,
 │                                                data_subset, sensitivity]
 ├─ critique              ← AdversarialCriticAgent (estimates + refutations + DAG → challenges)
 ├─ score                 ← deterministic confidence(estimates, refutations, challenges)
 ├─ render                ← branch on audience flag
 └─ seal                  ← audit_log.append(canonical(artifact))
END
```

**Why fan-out matters:** four estimators agreeing (CIs overlap) is a robustness
signal regardless of which method is "correct". They disagreeing is itself the
answer — confidence drops to "low". The user sees an honest output.

### 4.2 Confidence (deterministic, no LLM)

```python
def score_confidence(estimates, refutations, challenges) -> Literal["low","medium","high"]:
    refute_pass = sum(r.passed for r in refutations) / max(len(refutations), 1)
    ci_overlap  = pairwise_ci_overlap_rate(estimates)
    high_sev    = sum(1 for c in challenges if c.severity == "high")
    raw = 0.5 * refute_pass + 0.4 * ci_overlap - 0.3 * high_sev
    if raw > 0.7:  return "high"
    if raw > 0.4:  return "medium"
    return "low"
```

Pure function → reproducible, auditable, no LLM dependency on the trust signal.

### 4.3 Audit-seal contract

Every artifact has a chain-link entry:

```python
{
  "record_id": "ca_<uuid>",
  "ts": "2026-05-02T18:32:11.123Z",          # ISO-8601 UTC, Z suffix
  "service": "counterfactual_service",
  "actor": "<JWT principal sub or 'system'>",
  "request_hash": sha256(canonical(input_payload)),
  "artifact_hash": sha256(canonical(artifact_minus_audit_fields)),
  "schema_version": "<alembic_rev>",
  "dataset_fingerprint": "<sha256(source_bytes)>",
  "model_provenance": {
      "parser": "groq:llama-3.3-70b@T=0",
      "critic": "gemini:1.5-pro@T=0"
  },
  "seeds": {
      "ipw": 0xfeed_dead, "psm": 0xbeef_face,
      "placebo": 0xc0de, "subset": 0x7afe
  },                                         # derived from request_hash
  "critic_cache_key": "<sha256(request_hash, model_id, model_version)>",
  "regenerated_critic": false,
  "prev_hash": "<chain_prev>",
  "record_hash": sha256(canonical(self_minus_record_hash))
}
```

### 4.4 Canonical JSON rules (non-negotiable for hash reproducibility)

* Sort all keys recursively.
* Sort estimates by `method`, refutations by `refuter`, challenges by `(severity, sha1(text))`.
* Floats serialize as `f"{v:.6f}"` (six-decimal fixed precision).
* Datetimes: ISO-8601 UTC with explicit `Z` suffix. No timezone abbreviations.
* Drop `None`/null-valued keys before serializing (do not represent absence as null).

### 4.5 LLM-determinism wrinkle (the critic)

The adversarial critic is an LLM call. LLMs aren't strictly deterministic
even at `temperature=0`. **Solution: cache the critic's output keyed by
`(request_hash, model_id, model_version)`.**

* Replay reads from the cache — identical artifact byte-for-byte.
* Cache miss → critic re-runs → artifact gets `regenerated_critic: true`
  and the auditor view shows a side-by-side diff between the cached and
  newly-generated critique.
* Cache lives in the same WORM PVC as the audit log.

This is the right escape hatch: it admits LLMs aren't deterministic and
turns the limitation into a transparency signal instead of pretending the
problem doesn't exist.

### 4.6 Per-audience renderer

| Audience | Output shape |
|---|---|
| `operator` | Chat-card JSON: point estimate, CI, confidence, top 2 challenges, "see the debate" toggle |
| `auditor` | Operator output + full estimator table + all refutations + all challenges + signed PDF link |
| `analyst` | Auditor output + per-estimator effect-modifier breakdown + raw refutation outputs + seeds + notebook-friendly key order |

Engine is identical. The renderer dispatch is a 50-line function.

## 5. New code (Sprint 8)

| Module | Purpose | Est. LOC |
|---|---|---|
| `counterfactual_service/__init__.py` | Package marker | 0 |
| `counterfactual_service/schemas.py` | Pydantic types (Query, Estimate, Refutation, Challenge, Artifact) | 150 |
| `counterfactual_service/canonical.py` | Canonical JSON + sha256 helpers | 60 |
| `counterfactual_service/engine.py` | LangGraph sub-DAG; estimator/refuter fan-out; deterministic confidence; sealing | 320 |
| `counterfactual_service/renderers.py` | operator / auditor / analyst renderers | 120 |
| `counterfactual_service/main.py` | FastAPI app; job queue; SSE; lifecycle | 140 |
| `agents/specialists/counterfactual_parser_agent.py` | NL → Query (chat-side; in api_gateway, not the service) | 90 |
| `agents/specialists/adversarial_critic_agent.py` | Challenge generation against estimates + DAG | 110 |
| `api_gateway/routers/counterfactual.py` | Chat-router proxy + SSE relay | 100 |
| `mcp_servers/aura_mcp_server.py` | + 2 tool handlers (`counterfactual.run`, `counterfactual.get`) | 30 |
| `frontend/src/pages/Counterfactual.tsx` | Page + Card component + debate reveal | 220 |
| `frontend/src/components/CounterfactualCard.tsx` | Shared card component for chat reuse | 80 |
| `deploy/helm/aura/templates/counterfactual-deployment.yaml` | Deployment | 30 |
| `deploy/helm/aura/templates/counterfactual-service.yaml` | Service | 15 |
| `deploy/helm/aura/templates/counterfactual-configmap.yaml` | Env config | 20 |
| `aurabackend/tests/test_counterfactual_engine.py` | Unit + integration | 250 |
| `aurabackend/tests/test_counterfactual_canonical.py` | Canonical JSON round-trip | 80 |
| `aurabackend/tests/test_counterfactual_eval_gate.py` | Eval-gate layers 9 + 11 (causal correctness, adversarial detection) | 150 |
| `frontend/src/__tests__/CounterfactualCard.test.tsx` | Component test | 60 |
| **Total Sprint 8** | | **~2,025** |

## 6. Test strategy

### 6.1 Eval-gate extension (existing 8 layers + 3 new)

| Layer | Asserts |
|---|---|
| **9 — causal correctness** | Synthetic dataset with known ground-truth treatment effect; engine recovers within MAE bound (default 0.10 of true effect) |
| **10 — artifact reproducibility** *(Sprint 9)* | Submit job → record `audit_record_hash` → replay via record_hash → identical hash byte-for-byte |
| **11 — adversarial detection** | Confounded synthetic dataset + DAG missing the confounder → critic emits ≥ 1 high-severity challenge |

### 6.2 Unit tests

* **Parser:** NL → Query, edge cases (malformed, missing column, ambiguous treatment, no time window).
* **Renderer:** one artifact → three audience views, each schema-validated, identical core fields.
* **Confidence:** golden table of `(refute_pass, ci_overlap, high_sev) → expected confidence`.
* **Canonical JSON:** round-trip stability under shuffled key order, equivalent floats, mixed types.
* **Engine fan-out:** simulated estimator failures (one timeout, one exception) → engine returns partial result with structured warnings, not 500.

### 6.3 Integration tests

* Full job run with synthetic dataset (known causal effect) → engine recovers within tolerance.
* Replay (Sprint 9) → identical hash.
* Refutation regression: deliberately broken DAG → high-severity challenges in artifact.

## 7. Risk register

| # | Risk | Mitigation | Sprint |
|---|---|---|---|
| 1 | DoWhy assumes numeric outcomes | Scope v1 to numeric only; document; add EconML in S11+ | 8 |
| 2 | User-supplied DAG quality drives answer quality | Critic flags sparse/incomplete DAGs as high-severity; engine refuses unidentifiable estimands | 8 |
| 3 | 4 estimators × 4 refuters → 16 computations; 30-120s on 100k+ rows | Per-step 30s timeout; job-level 5min cap; partial results with structured "step X timed out" annotations | 8 |
| 4 | DoWhy PSM holds matched dataset in memory (~400MB on 1M rows × 50 cols) | Helm resource caps; pre-flight row-count gate; reject jobs over configurable threshold with structured error | 8 |
| 5 | MCP tool exposure → unauthenticated job submission | Same `AURA_MCP_API_KEY` gate as `duckdb.query`; per-key rate limit (10 jobs/hour default) | 8 |
| 6 | Audit log volume — counterfactual artifacts ~5-50KB each | Already daily-rotated JSONL on WORM PVC; existing 50Gi budget covers ≥ 1k artifacts/day for a year | 8 |
| 7 | Replay needs original dataset; datasets evolve | `dataset_fingerprint` pins exact bytes; replay fails closed if fingerprint mismatch; auditor view shows "dataset modified since" warning | 9 |
| 8 | LLM critic non-determinism breaks reproducibility | Critic-cache keyed by `(request_hash, model_id, version)`; cache miss → `regenerated_critic: true` flag | 8 |

## 8. Success criteria (Sprint 8)

* Backend `pytest` suite passes including 3 new eval-gate layers.
* `/counterfactual/info` returns `{"dowhy_available": true, "engine_version": "0.1.0"}` on a fresh boot.
* `POST /counterfactual/jobs` with the example payload (Section 3.2) returns a `job_id` within 200 ms.
* SSE stream emits structured progress events (`identify`, `estimate.linear_regression`, ..., `seal`).
* Final job state contains a `CounterfactualArtifact` with all four estimators, all four refutations, ≥ 1 challenge, a confidence label, and an `audit_record_hash` that's findable in the TRAIGA log.
* Frontend Counterfactual page renders the artifact's operator card; "see the debate" reveals challenges.
* MCP tools `counterfactual.run` and `counterfactual.get` callable from Claude Code with API key.
* Helm chart synthesises cleanly (`helm template deploy/helm/aura | kubectl apply --dry-run=server`).
* Coverage gate stays ≥ 60% (engine module above 70%).

## 9. Sprint sequence at a glance

| Sprint | Tier | Demo |
|---|---|---|
| 8 | Operator | Chat → "What would Q3 have been if we hadn't raised prices?" → Counterfactual Card with confidence + debate |
| 9 | Auditor | Artifact → signed PDF → tampered chain caught by `audit-verifier-cronjob` |
| 10 | Analyst | `aura.replay(record_hash)` in Jupyter → drill into estimates programmatically |
