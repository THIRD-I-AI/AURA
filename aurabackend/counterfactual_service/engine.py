"""
Counterfactual Audit Engine — orchestration layer.

Estimator + refuter fan-out lives here for cohesion: they share the same
treatment/outcome/data inputs and the engine is the only consumer.

Contract surface (the rest of the service depends on these names):

* ``score_confidence(estimates, refutations, challenges) -> "low|medium|high"``
* ``run_estimators(df, treatment, outcome, dag) -> List[CounterfactualEstimate]``
* ``run_refuters(df, treatment, outcome, dag)   -> List[RefutationResult]``
* ``run_job(query, df) -> CounterfactualArtifact``
* ``dowhy_available() -> bool``
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from itertools import combinations
from typing import Any, Dict, List, Literal, Optional

import pandas as pd

from . import critic_cache, persistence, signing
from .canonical import canonical_dumps, sha256_canonical
from .schemas import (
    AdversarialChallenge,
    CounterfactualArtifact,
    CounterfactualEstimate,
    CounterfactualQuery,
    EstimatorMethod,
    InterventionSpec,
    OutcomeSpec,
    RefutationResult,
    RefuterName,
    Severity,
)

logger = logging.getLogger("aura.counterfactual.engine")


# ── Optional dep ──────────────────────────────────────────────────────

try:
    from dowhy import CausalModel  # type: ignore
    _DOWHY_AVAILABLE = True
except ImportError:  # pragma: no cover
    CausalModel = None  # type: ignore[assignment]
    _DOWHY_AVAILABLE = False


def dowhy_available() -> bool:
    return _DOWHY_AVAILABLE


# ── Confidence scoring (deterministic, no LLM) ────────────────────────

def _ci_pair_overlap(a: CounterfactualEstimate, b: CounterfactualEstimate) -> bool:
    return not (a.ci_upper < b.ci_lower or b.ci_upper < a.ci_lower)


def pairwise_ci_overlap_rate(estimates: List[CounterfactualEstimate]) -> float:
    """Fraction of estimator pairs whose 95% CIs overlap.

    With <2 valid estimates the rate is undefined; we return 1.0 for a
    single valid estimate (no disagreement to penalise) and 0.0 for none
    (no information).
    """
    valid = [e for e in estimates if e.error is None]
    if len(valid) < 2:
        return 1.0 if valid else 0.0
    pairs = list(combinations(valid, 2))
    overlaps = sum(_ci_pair_overlap(a, b) for a, b in pairs)
    return overlaps / len(pairs)


def score_confidence(
    estimates: List[CounterfactualEstimate],
    refutations: List[RefutationResult],
    challenges: List[AdversarialChallenge],
) -> Severity:
    """Pure deterministic confidence: 0.5*refute_pass + 0.4*ci_overlap - 0.3*high_sev."""
    refute_pass = (
        sum(r.passed for r in refutations) / len(refutations) if refutations else 0.0
    )
    ci_overlap = pairwise_ci_overlap_rate(estimates)
    high_sev = sum(1 for c in challenges if c.severity == "high")
    raw = 0.5 * refute_pass + 0.4 * ci_overlap - 0.3 * high_sev
    if raw > 0.7:
        return "high"
    if raw > 0.4:
        return "medium"
    return "low"


# ── DoWhy method registries ───────────────────────────────────────────

_DOWHY_ESTIMATOR_METHODS: Dict[EstimatorMethod, str] = {
    "linear_regression": "backdoor.linear_regression",
    "ipw": "backdoor.propensity_score_weighting",
    "psm": "backdoor.propensity_score_matching",
    # Double-ML stub: backdoor.linear_regression with all confounders
    # forced into the adjustment set. Real EconML DRLearner lands in S11+.
    "double_ml": "backdoor.linear_regression",
}

_DOWHY_REFUTER_METHODS: Dict[RefuterName, str] = {
    "random_common_cause": "random_common_cause",
    "placebo": "placebo_treatment_refuter",
    "data_subset": "data_subset_refuter",
    "sensitivity": "add_unobserved_common_cause",
}


# ── Causal-model construction ─────────────────────────────────────────

def _build_causal_model(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
) -> Any:
    if not _DOWHY_AVAILABLE:
        raise RuntimeError("dowhy is not installed in this environment")
    edges = dag.get("edges", [])
    edge_lines = "\n".join(f'  "{src}" -> "{dst}";' for src, dst in edges)
    graph = f'digraph {{\n{edge_lines}\n}}'
    return CausalModel(
        data=df,
        treatment=treatment.column,
        outcome=outcome.column,
        graph=graph,
    )


# ── Estimator fan-out ─────────────────────────────────────────────────

def _run_one_estimator(
    method_key: EstimatorMethod,
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
) -> CounterfactualEstimate:
    t0 = time.time()
    try:
        model = _build_causal_model(df, treatment, outcome, dag)
        identified = model.identify_effect(proceed_when_unidentifiable=True)
        est = model.estimate_effect(
            identified,
            method_name=_DOWHY_ESTIMATOR_METHODS[method_key],
            test_significance=True,
            confidence_intervals=True,
        )
        point = float(est.value)
        ci_attr = getattr(est, "get_confidence_intervals", None)
        ci = None
        if callable(ci_attr):
            try:
                ci = ci_attr()
            except Exception:
                ci = None
        if ci is not None:
            try:
                if hasattr(ci, "tolist"):
                    ci = ci.tolist()
                if isinstance(ci, (list, tuple)) and len(ci) >= 2:
                    flat = ci[0] if isinstance(ci[0], (list, tuple)) else ci
                    lo, hi = float(flat[0]), float(flat[1])
                else:
                    raise ValueError("unexpected CI shape")
            except Exception:
                ci = None
        if ci is None:
            stderr = float(getattr(est, "stderr", 0.0) or 0.0)
            lo, hi = point - 2 * stderr, point + 2 * stderr
        if hi < lo:
            lo, hi = hi, lo
        return CounterfactualEstimate(
            method=method_key,
            point=point,
            ci_lower=lo,
            ci_upper=hi,
            n_samples=len(df),
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        logger.warning("Estimator %s failed: %s", method_key, exc)
        return CounterfactualEstimate(
            method=method_key,
            point=0.0, ci_lower=0.0, ci_upper=0.0,
            n_samples=len(df),
            elapsed_ms=(time.time() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_estimators(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
    methods: Optional[List[EstimatorMethod]] = None,
    timeout_s: float = 30.0,
) -> List[CounterfactualEstimate]:
    """Fan-out estimator runs in a thread pool with per-step timeout.

    Always returns one ``CounterfactualEstimate`` per requested method;
    failures and timeouts are surfaced via the ``error`` field rather
    than raising. Output is sorted by method name for hash-stable
    artifacts.
    """
    chosen: List[EstimatorMethod] = methods or list(_DOWHY_ESTIMATOR_METHODS.keys())
    loop = asyncio.get_event_loop()

    async def _one(m: EstimatorMethod) -> CounterfactualEstimate:
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run_one_estimator, m, df, treatment, outcome, dag),
                timeout_s,
            )
        except asyncio.TimeoutError:
            return CounterfactualEstimate(
                method=m, point=0.0, ci_lower=0.0, ci_upper=0.0,
                n_samples=len(df), elapsed_ms=timeout_s * 1000,
                error=f"timeout after {timeout_s}s",
            )

    results = await asyncio.gather(*(_one(m) for m in chosen))
    return sorted(results, key=lambda e: e.method)


# ── Refuter fan-out ───────────────────────────────────────────────────

def _refuter_passed(refuter: RefuterName, baseline: float, refuted: float) -> bool:
    """Pass criterion is method-specific.

    * placebo: refuted estimate should be near zero (treatment shuffled,
      so any leftover effect is noise).
    * everything else: refuted estimate should stay close to baseline.

    Threshold is 20% of |baseline| or 0.1 absolute, whichever is larger
    — matches DoWhy convention and avoids divide-by-near-zero blow-ups.
    """
    threshold = max(abs(baseline) * 0.2, 0.1)
    if refuter == "placebo":
        return abs(refuted) < threshold
    return abs(refuted - baseline) < threshold


def _run_one_refuter(
    refuter_key: RefuterName,
    model: Any,
    identified: Any,
    baseline_estimate: Any,
) -> RefutationResult:
    t0 = time.time()
    try:
        result = model.refute_estimate(
            identified,
            baseline_estimate,
            method_name=_DOWHY_REFUTER_METHODS[refuter_key],
        )
        new_value: Optional[float] = None
        new_attr = getattr(result, "new_effect", None)
        if new_attr is not None:
            try:
                new_value = float(new_attr)
            except Exception:
                new_value = None
        p_value: Optional[float] = None
        ref_result = getattr(result, "refutation_result", None)
        if isinstance(ref_result, dict):
            p_raw = ref_result.get("p_value")
            try:
                p_value = float(p_raw) if p_raw is not None else None
            except Exception:
                p_value = None
        baseline_val = float(getattr(baseline_estimate, "value", 0.0) or 0.0)
        passed = (
            _refuter_passed(refuter_key, baseline_val, new_value)
            if new_value is not None
            else False
        )
        return RefutationResult(
            refuter=refuter_key,
            estimate_after=new_value,
            p_value=p_value,
            passed=passed,
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        logger.warning("Refuter %s failed: %s", refuter_key, exc)
        return RefutationResult(
            refuter=refuter_key,
            estimate_after=None, p_value=None,
            passed=False,
            elapsed_ms=(time.time() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_refuters(
    df: pd.DataFrame,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    dag: dict,
    refuters: Optional[List[RefuterName]] = None,
    timeout_s: float = 30.0,
) -> List[RefutationResult]:
    """Fan-out refuter runs against a single baseline estimate.

    Always returns one ``RefutationResult`` per requested refuter; sorted
    by refuter name for hash stability.
    """
    chosen: List[RefuterName] = refuters or list(_DOWHY_REFUTER_METHODS.keys())
    if not _DOWHY_AVAILABLE:
        return [
            RefutationResult(refuter=r, passed=False, error="dowhy not installed")
            for r in chosen
        ]

    try:
        model = _build_causal_model(df, treatment, outcome, dag)
        identified = model.identify_effect(proceed_when_unidentifiable=True)
        baseline = model.estimate_effect(
            identified,
            method_name=_DOWHY_ESTIMATOR_METHODS["linear_regression"],
        )
    except Exception as exc:
        logger.warning("Baseline for refuters failed: %s", exc)
        return [
            RefutationResult(refuter=r, passed=False,
                             error=f"baseline failed: {type(exc).__name__}: {exc}")
            for r in chosen
        ]

    loop = asyncio.get_event_loop()

    async def _one(r: RefuterName) -> RefutationResult:
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run_one_refuter, r, model, identified, baseline),
                timeout_s,
            )
        except asyncio.TimeoutError:
            return RefutationResult(
                refuter=r, passed=False,
                elapsed_ms=timeout_s * 1000,
                error=f"timeout after {timeout_s}s",
            )

    results = await asyncio.gather(*(_one(r) for r in chosen))
    return sorted(results, key=lambda r: r.refuter)


# ── End-to-end orchestration ──────────────────────────────────────────

def _dataset_fingerprint(df: pd.DataFrame) -> str:
    """Stable sha256 over (sorted columns, dtypes, head/tail rows, length).

    Two structurally-identical dataframes from different file paths will
    fingerprint identically; any column rename or dtype change produces a
    new fingerprint. Sufficient for replay's "did the dataset move?" check.
    """
    cols = sorted(df.columns.tolist())
    h = hashlib.sha256()
    h.update(",".join(cols).encode("utf-8"))
    for c in cols:
        h.update(str(df[c].dtype).encode("utf-8"))
    if len(df):
        h.update(canonical_dumps(df.head(3).to_dict(orient="records")).encode("utf-8"))
        h.update(canonical_dumps(df.tail(3).to_dict(orient="records")).encode("utf-8"))
    h.update(str(len(df)).encode("utf-8"))
    return h.hexdigest()


async def _run_critic(
    estimates: List[CounterfactualEstimate],
    refutations: List[RefutationResult],
    dag: dict,
    treatment: InterventionSpec,
    outcome: OutcomeSpec,
    *,
    request_hash: str,
) -> tuple[List[AdversarialChallenge], bool]:
    """Run the adversarial critic, with replay-determinism caching.

    Returns ``(challenges, regenerated)`` where ``regenerated`` is True
    if the critic re-ran (cache miss). Replay flows through cache hits
    so the artifact byte-rehashes identically.
    """
    # Identify the model so the cache key is sensitive to provider drift.
    # Agent-side late import avoids dragging shared.budget into module
    # init.
    from agents.base import AgentContext
    from agents.specialists.adversarial_critic_agent import AdversarialCriticAgent

    agent = AdversarialCriticAgent()
    model_id = getattr(agent.llm, "model", "") or ""
    model_version = getattr(agent.llm, "model_version", "") or "v1"

    cache_k = critic_cache.cache_key(
        request_hash=request_hash, model_id=str(model_id), model_version=str(model_version),
    )
    cached = critic_cache.get(cache_k)
    if cached is not None:
        return [AdversarialChallenge(**c) for c in cached], False

    ctx = AgentContext(
        user_prompt="critique counterfactual",
        task_description="Find missing confounders, identifiability failures, and "
                         "estimator-refutation contradictions.",
        upstream_results={
            "estimates": [e.model_dump() for e in estimates],
            "refutations": [r.model_dump() for r in refutations],
            "dag": dag,
            "treatment": treatment.model_dump(),
            "outcome": outcome.model_dump(),
        },
    )
    res = await agent.execute(ctx)
    raw = res.output.get("challenges", []) if res.succeeded else []

    # Persist into the cache so future replays hit it. This is a
    # best-effort write; a cache miss next time is recoverable as long
    # as the engine re-runs and the new bytes match.
    try:
        critic_cache.put(cache_k, raw)
    except Exception as exc:  # pragma: no cover
        logger.warning("Critic cache write failed (non-fatal): %s", exc)

    return [AdversarialChallenge(**c) for c in raw], True


_HASH_EXCLUDE_FIELDS = {
    "audit_record_hash",
    "rendered",
    "signature_b64",
    "signature_status",
    "signing_key_source",
    # record_id is uuid-random and uncorrelated with the inputs — exclude
    # so two jobs with identical inputs produce the same artifact_hash
    # regardless of the random ID assigned at submission time.
    "record_id",
    # regenerated_critic is *metadata about how the answer was produced*
    # (cache hit vs miss), not part of the answer itself. Excluding it
    # means the artifact hash is byte-stable across replay regardless of
    # whether the critic-cache survived since the original sealing.
    "regenerated_critic",
}


def _request_hash(query: CounterfactualQuery, dataset_fingerprint: str) -> str:
    """Stable hash of the user-controllable inputs.

    Used as the cache key for the critic and as the seed-derivation
    base. Must NOT depend on record_id, audit_record_hash, or anything
    populated downstream by the engine.
    """
    return sha256_canonical({
        "query": query.model_dump(mode="json"),
        "dataset_fingerprint": dataset_fingerprint,
    })


async def run_job(query: CounterfactualQuery, df: pd.DataFrame) -> CounterfactualArtifact:
    """Full engine: estimate → refute → critique (cached) → score → sign → persist → seal.

    Returns the artifact with ``audit_record_hash`` and (when signing is
    available) ``signature_b64`` populated. Caller is responsible for
    renderer dispatch (engine is renderer-agnostic).
    """
    # Defensive copy — DoWhy's PSM and IPW estimators mutate the input
    # DataFrame (attach propensity scores, weights, matched-pair labels),
    # which would change dataset_fingerprint on a subsequent run with the
    # same logical input.
    df = df.copy()
    fingerprint = _dataset_fingerprint(df)
    req_hash = _request_hash(query, fingerprint)

    estimates = await run_estimators(df, query.treatment, query.outcome, query.dag.model_dump())
    refutations = await run_refuters(df, query.treatment, query.outcome, query.dag.model_dump())
    challenges_unsorted, regenerated = await _run_critic(
        estimates, refutations, query.dag.model_dump(),
        query.treatment, query.outcome,
        request_hash=req_hash,
    )
    # SHA-1 is only used as a stable, deterministic tie-breaker on the
    # challenge text — purely so two artifacts with identical (severity,
    # text) lists sort identically across runs. ``usedforsecurity=False``
    # signals to security scanners (bandit) that this is not a
    # cryptographic use and silences the B324 warning.
    challenges = sorted(
        challenges_unsorted,
        key=lambda c: (
            c.severity,
            hashlib.sha1(c.text.encode("utf-8"), usedforsecurity=False).hexdigest(),
        ),
    )

    record_id = f"ca_{uuid.uuid4().hex[:12]}"
    schema_version = "v1"   # Sprint 10: derive from current alembic head

    artifact = CounterfactualArtifact(
        record_id=record_id,
        query=query,
        estimates=estimates,
        refutations=refutations,
        challenges=challenges,
        confidence=score_confidence(estimates, refutations, challenges),
        schema_version=schema_version,
        dataset_fingerprint=fingerprint,
        regenerated_critic=regenerated,
    )

    # Compute artifact_hash over the artifact MINUS audit/render/signature
    # fields. record_id is also excluded so byte-stable replay is possible
    # regardless of which ca_<uuid> the original submission was assigned.
    payload = artifact.model_dump(mode="json", exclude=_HASH_EXCLUDE_FIELDS)
    artifact_hash = sha256_canonical(payload)
    artifact.audit_record_hash = artifact_hash

    # Sign the canonical bytes of the (still-hash-stable) payload. The
    # signed bytes are exactly what sha256_canonical hashed, so a verifier
    # can independently reconstruct what was signed.
    sig_b64 = signing.sign_bytes(canonical_dumps(payload).encode("utf-8"))
    if sig_b64 is not None:
        artifact.signature_b64 = sig_b64
        artifact.signature_status = "signed"
        artifact.signing_key_source = signing.signing_key_source()
    else:
        artifact.signature_status = "unsigned"

    # Persist the full artifact (with audit_record_hash + signature) so
    # replay returns byte-identical content.
    try:
        full_persistable = artifact.model_dump(mode="json")
        persistence.write_artifact(artifact_hash, full_persistable)
        if sig_b64 is not None:
            persistence.write_signature(artifact_hash, sig_b64)
    except Exception as exc:  # pragma: no cover
        logger.warning("Artifact persistence failed (non-fatal): %s", exc)

    # Seal in TRAIGA audit log (best-effort; engine never blocks on audit).
    try:
        from shared.audit_log import AUDIT_ENABLED  # type: ignore
        if AUDIT_ENABLED:
            from shared.audit_log import audit_request  # type: ignore
            audit_request(
                user="counterfactual_service",
                method="POST",
                path="/counterfactual/jobs",
                meta={
                    "record_id": record_id,
                    "artifact_hash": artifact_hash,
                    "schema_version": schema_version,
                    "dataset_fingerprint": fingerprint,
                    "signature_status": artifact.signature_status,
                    "regenerated_critic": regenerated,
                },
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("Audit seal failed (non-fatal): %s", exc)

    return artifact
