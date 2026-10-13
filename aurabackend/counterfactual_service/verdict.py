"""Significance-aware verdict for the audited effect.

ONE canonical rule, shared by the PDF renderer, the chat/operator headline, and
(via the artifact's rendered block) the web certificate — so a signed compliance
artifact can never overclaim a directional effect whose confidence interval
straddles zero, nor self-contradict across surfaces. Mirrors the frontend rule
shipped in S31f (frontend/src/audit/Certificate.tsx::verdict).
"""
from __future__ import annotations

from typing import Any, Dict, List

# Materiality is judged on the effect relative to the outcome's own spread: the
# standardised effect d = point / sd(outcome) that the sensitivity analysis already
# attaches to each estimate. BUG-402: the cutoff used to be 0.02 in raw outcome
# units for every outcome, so a 150-basis-point effect on a rate stored as a decimal
# was certified "not material" and a five-cent effect on a dollar amount "detected".
MATERIAL_STANDARDISED_EFFECT = 0.05

# Fallback for an estimate with no usable standardised effect (sensitivity analysis
# unavailable, or a constant outcome): the absolute size in outcome units. Also the
# rule the frontend applies to an artifact that carries no signed verdict.
MATERIAL_THRESHOLD = 0.02

_LABELS = {
    "empty": "Audit complete — see estimator detail.",
    "not_material": "No material disparate impact detected after causal adjustment.",
    "not_significant": (
        "A point estimate suggests an effect, but it is not statistically "
        "significant after adjustment (95% intervals include zero)."
    ),
    "inconsistent": (
        "The estimators disagree on the direction of the effect; no conclusion "
        "about disparate impact is drawn."
    ),
    "detected": "Disparate impact detected after causal adjustment.",
}


def _get(est: Any, key: str) -> Any:
    return est.get(key) if isinstance(est, dict) else getattr(est, key, None)


def _to_float(value: Any):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def _ci_excludes_zero(lo: float, hi: float) -> bool:
    return (lo > 0 and hi > 0) or (lo < 0 and hi < 0)


def _is_material(est: Any, point: float) -> bool:
    sensitivity = _get(est, "sensitivity")
    d = _to_float(_get(sensitivity, "standardised_effect_d")) if sensitivity is not None else None
    # d is reported as 0 for a constant outcome, where it carries no information.
    if d is not None and (d != 0.0 or point == 0.0):
        return abs(d) >= MATERIAL_STANDARDISED_EFFECT
    return abs(point) >= MATERIAL_THRESHOLD


# "v1": the original rule -- |mean of the points| against 0.02 in raw outcome units.
# Kept so an artifact sealed under it re-renders with the verdict it was issued with.
# "v2": scale-aware, per-estimator materiality and a direction check (BUG-402).
LEGACY_RULE = "v1"
CURRENT_RULE = "v2"


def significance_verdict(estimates: List[Any], rule: str = CURRENT_RULE) -> Dict[str, Any]:
    """Classify the audited effect from estimator points + 95% CIs.

    Accepts ``CounterfactualEstimate`` models OR plain dicts (the replay path
    serialises numbers as strings, so values are coerced). Returns
    ``{status, label, avg, n_usable}`` with status in
    {empty, not_material, not_significant, inconsistent, detected}.

    Each estimator is judged on its own: an effect is material when any estimator's
    is (averaging signed estimates let opposite signs cancel to "not material"). A
    material effect is only "detected" when every estimator's 95% CI excludes zero
    and they agree on its direction.
    """
    usable = [
        e for e in estimates
        if _get(e, "error") is None and _to_float(_get(e, "point")) is not None
    ]
    if not usable:
        return {"status": "empty", "label": _LABELS["empty"], "avg": None, "n_usable": 0}

    points = [_to_float(_get(e, "point")) for e in usable]
    avg = sum(points) / len(points)

    if rule == LEGACY_RULE:
        material = abs(avg) >= MATERIAL_THRESHOLD
    else:
        material = any(_is_material(e, p) for e, p in zip(usable, points))

    if not material:
        status = "not_material"
    else:
        with_ci = []
        for e in usable:
            lo = _to_float(_get(e, "ci_lower"))
            hi = _to_float(_get(e, "ci_upper"))
            if lo is not None and hi is not None:
                with_ci.append((lo, hi))
        if with_ci and not all(_ci_excludes_zero(lo, hi) for lo, hi in with_ci):
            status = "not_significant"
        elif rule != LEGACY_RULE and any(p > 0 for p in points) and any(p < 0 for p in points):
            status = "inconsistent"
        else:
            status = "detected"

    return {"status": status, "label": _LABELS[status], "avg": avg, "n_usable": len(usable), "rule": rule}


def rule_for(artifact: Any) -> str:
    """The verdict rule an artifact (model or stored dict) was sealed under."""
    applied = _get(artifact, "applied")
    rule = _get(applied, "verdict_rule") if applied is not None else None
    return rule or LEGACY_RULE
