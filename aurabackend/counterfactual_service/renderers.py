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


def _operator(art: CounterfactualArtifact) -> Dict[str, Any]:
    valid = [e for e in art.estimates if e.error is None]
    point = sum(e.point for e in valid) / len(valid) if valid else 0.0
    ci_lo = min((e.ci_lower for e in valid), default=0.0)
    ci_hi = max((e.ci_upper for e in valid), default=0.0)
    top_challenges = [c.model_dump() for c in art.challenges[:2]]
    return {
        "record_id": art.record_id,
        "headline": _headline(art),
        "point_estimate": point,
        "ci": [ci_lo, ci_hi],
        "confidence": art.confidence,
        "top_challenges": top_challenges,
        "audit_record_hash": art.audit_record_hash,
    }


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
