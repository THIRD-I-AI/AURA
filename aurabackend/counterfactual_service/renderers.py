"""Per-audience renderer dispatch for ``CounterfactualArtifact``.

Engine produces one canonical artifact; this module produces three
shapes for the three audiences. The output of every renderer is a
plain ``dict`` (not a Pydantic model) so it can be JSON-serialised
into a chat response without further conversion.
"""
from __future__ import annotations

from typing import Any, Dict

from .schemas import Audience, CounterfactualArtifact


def _headline(art: CounterfactualArtifact) -> str:
    valid = [e for e in art.estimates if e.error is None]
    if not valid:
        return "Estimation failed across all methods."
    avg_point = sum(e.point for e in valid) / len(valid)
    direction = "increase" if avg_point > 0 else "decrease"
    return (
        f"Counterfactual {direction} of about {avg_point:+.2f} on "
        f"{art.query.outcome.column} (confidence: {art.confidence})."
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
    out: Dict[str, Any] = {
        "record_id": art.record_id,
        "headline": _headline(art),
        "point_estimate": point,
        "ci": [ci_lo, ci_hi],
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
