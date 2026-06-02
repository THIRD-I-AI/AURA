"""Per-audience renderer dispatch for ``CounterfactualArtifact``.

Engine produces one canonical artifact; this module produces three
shapes for the three audiences. The output of every renderer is a
plain ``dict`` (not a Pydantic model) so it can be JSON-serialised
into a chat response without further conversion.
"""
from __future__ import annotations

from typing import Any, Dict

from .schemas import Audience, CounterfactualArtifact
from .verdict import significance_verdict


def _headline(art: CounterfactualArtifact) -> str:
    valid = [e for e in art.estimates if e.error is None]
    if not valid:
        return "Estimation failed across all methods."
    # Lead with the significance-aware verdict (shared rule) so the chat/operator
    # headline cannot overclaim a directional effect whose CI straddles zero.
    label = significance_verdict(art.estimates)["label"]
    avg_point = sum(e.point for e in valid) / len(valid)
    return (
        f"{label} (average effect {avg_point:+.2f} on "
        f"{art.query.outcome.column}; confidence: {art.confidence})."
    )


def _propensity_summary(art: CounterfactualArtifact) -> Dict[str, Any] | None:
    """Sprint 14: extract the DR-style propensity diagnostics into a
    small inline shape for the operator card. Returns None when no
    estimator surfaced propensity data (i.e. econml fallback path)."""
    dr_with_diag = next(
        (e for e in art.estimates
         if e.propensity_diagnostics is not None and e.error is None),
        None,
    )
    if dr_with_diag is None or dr_with_diag.propensity_diagnostics is None:
        return None
    diag = dr_with_diag.propensity_diagnostics
    frac = diag.n_extreme / diag.n_total if diag.n_total > 0 else 0.0
    # Match the engine's auto-challenge thresholds so the operator card
    # and the challenge text agree on what "fragile" means.
    if frac > 0.10 or diag.quantiles.get("p05", 1.0) < 0.05 or diag.quantiles.get("p95", 0.0) > 0.95:
        fragility = "red"
    elif frac > 0.05 or diag.quantiles.get("p05", 1.0) < 0.10 or diag.quantiles.get("p95", 0.0) > 0.90:
        fragility = "amber"
    else:
        fragility = "ok"
    return {
        "method": dr_with_diag.method,
        "fragility": fragility,
        "n_extreme": diag.n_extreme,
        "n_total": diag.n_total,
        "p05": diag.quantiles.get("p05", 0.0),
        "p25": diag.quantiles.get("p25", 0.0),
        "p50": diag.quantiles.get("p50", 0.0),
        "p75": diag.quantiles.get("p75", 0.0),
        "p95": diag.quantiles.get("p95", 0.0),
        "mean": diag.mean,
    }


def _cate_distribution_summary(art: CounterfactualArtifact) -> Dict[str, Any] | None:
    """Sprint 15: extract per-row CATE quantiles from any estimator
    that ran the non-parametric ForestDR path. Returns None when no
    estimator surfaced ``cate_distribution`` (LinearDR / DoWhy paths
    populate only a scalar point + CI, no heterogeneity vector).

    The heterogeneity flag uses the inter-decile spread ratio:

      * ``low``       — IDR / |point| ≤ 1.0 (relatively homogeneous;
                        the population behaves like one group)
      * ``moderate``  — IDR / |point| in (1.0, 2.0] (some heterogeneity
                        worth surfacing but not pathological)
      * ``high``      — IDR / |point| > 2.0 (population subgroups have
                        meaningfully different treatment responses —
                        the ATE is an average of distinct effects)

    The auditor / operator reading this should treat ``high`` as a
    signal that one-number-summarising the effect is misleading;
    drilling into the X-subgroup level is the right next step.
    """
    src = next(
        (e for e in art.estimates
         if e.cate_distribution is not None and e.error is None),
        None,
    )
    if src is None or not src.cate_distribution:
        return None
    quantiles = list(src.cate_distribution)
    # Inter-decile spread = p95 - p05 (first and last of the 10 stored
    # quantiles by construction in the engine).
    idr = quantiles[-1] - quantiles[0]
    point = src.point
    ratio = abs(idr) / max(abs(point), 1e-9)
    if ratio > 2.0:
        heterogeneity = "high"
    elif ratio > 1.0:
        heterogeneity = "moderate"
    else:
        heterogeneity = "low"
    return {
        "method": src.method,
        "quantiles": quantiles,
        "point": point,
        "ci_lower": src.ci_lower,
        "ci_upper": src.ci_upper,
        "idr": idr,
        "heterogeneity": heterogeneity,
    }


def _sensitivity_band(art: CounterfactualArtifact) -> Dict[str, Any] | None:
    """Sprint 14: collapse the refutation outputs into a per-refuter
    perturbation summary the operator card can render as a horizontal
    band. Baseline = average of valid estimator points; each refuter
    contributes its (refuter_name, estimate_after, passed) row.
    Returns None when no refuter ran successfully."""
    valid_refs = [r for r in art.refutations if r.estimate_after is not None]
    if not valid_refs:
        return None
    valid_ests = [e for e in art.estimates if e.error is None]
    baseline = (
        sum(e.point for e in valid_ests) / len(valid_ests)
        if valid_ests else 0.0
    )
    return {
        "baseline": baseline,
        "perturbations": [
            {
                "refuter": r.refuter,
                "estimate_after": r.estimate_after,
                "passed": r.passed,
            }
            for r in valid_refs
        ],
    }


def _operator(art: CounterfactualArtifact) -> Dict[str, Any]:
    valid = [e for e in art.estimates if e.error is None]
    point = sum(e.point for e in valid) / len(valid) if valid else 0.0
    ci_lo = min((e.ci_lower for e in valid), default=0.0)
    ci_hi = max((e.ci_upper for e in valid), default=0.0)
    top_challenges = [c.model_dump() for c in art.challenges[:2]]
    # Sprint 16: aggregate ci_method across valid estimators so the
    # operator card can show the strongest contract in force. The
    # priority is "conformal > asymptotic" — conformal is the
    # distribution-free finite-sample guarantee, asymptotic is the
    # large-sample-under-correctly-specified-nuisance guarantee. If
    # any DR estimator ran with conformal calibration AND succeeded,
    # the conformal contract is what the operator sees.
    methods_seen = {e.ci_method for e in valid}
    if "conformal" in methods_seen and len(methods_seen) > 1:
        ci_method_overall = "mixed"
    elif methods_seen == {"conformal"}:
        ci_method_overall = "conformal"
    else:
        ci_method_overall = "asymptotic"
    out: Dict[str, Any] = {
        "record_id": art.record_id,
        "headline": _headline(art),
        "verdict": significance_verdict(art.estimates),
        "point_estimate": point,
        "ci": [ci_lo, ci_hi],
        "ci_method": ci_method_overall,
        "confidence": art.confidence,
        "top_challenges": top_challenges,
        "audit_record_hash": art.audit_record_hash,
    }
    # Sprint 14 additions — both optional so old fixtures still match.
    prop = _propensity_summary(art)
    if prop is not None:
        out["propensity_summary"] = prop
    sens = _sensitivity_band(art)
    if sens is not None:
        out["sensitivity_band"] = sens
    # Sprint 15 addition — only present when ForestDRLearner ran.
    cate = _cate_distribution_summary(art)
    if cate is not None:
        out["cate_distribution_summary"] = cate
    return out


def _auditor(art: CounterfactualArtifact) -> Dict[str, Any]:
    base = _operator(art)
    base.update({
        "estimates_full":      [e.model_dump() for e in art.estimates],
        "refutations_full":    [r.model_dump() for r in art.refutations],
        "all_challenges":      [c.model_dump() for c in art.challenges],
        "schema_version":      art.schema_version,
        "dataset_fingerprint": art.dataset_fingerprint,
        "regenerated_critic":  art.regenerated_critic,
    })
    return base


def _analyst(art: CounterfactualArtifact) -> Dict[str, Any]:
    base = _auditor(art)
    base["raw_artifact"] = art.model_dump(mode="json")
    return base


def render(art: CounterfactualArtifact, audience: Audience) -> Dict[str, Any]:
    if audience == "operator":
        return _operator(art)
    if audience == "auditor":
        return _auditor(art)
    if audience == "analyst":
        return _analyst(art)
    raise ValueError(f"unknown audience: {audience!r}")
