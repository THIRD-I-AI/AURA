"""Significance-aware verdict for the audited effect.

ONE canonical rule, shared by the PDF renderer, the chat/operator headline, and
(via the artifact's rendered block) the web certificate — so a signed compliance
artifact can never overclaim a directional effect whose confidence interval
straddles zero, nor self-contradict across surfaces. Mirrors the frontend rule
shipped in S31f (frontend/src/audit/Certificate.tsx::verdict).
"""
from __future__ import annotations

from typing import Any, Dict, List

# A point estimate below this absolute size is treated as no material effect,
# regardless of significance. Kept in lockstep with the frontend threshold.
MATERIAL_THRESHOLD = 0.02

_LABELS = {
    "empty": "Audit complete — see estimator detail.",
    "not_material": "No material disparate impact detected after causal adjustment.",
    "not_significant": (
        "A point estimate suggests an effect, but it is not statistically "
        "significant after adjustment (95% intervals include zero)."
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


def significance_verdict(estimates: List[Any]) -> Dict[str, Any]:
    """Classify the audited effect from estimator points + 95% CIs.

    Accepts ``CounterfactualEstimate`` models OR plain dicts (the replay path
    serialises numbers as strings, so values are coerced). Returns
    ``{status, label, avg, n_usable}`` with status in
    {empty, not_material, not_significant, detected}. A material point estimate
    is only "detected" when every estimator's 95% CI excludes zero.
    """
    usable = [
        e for e in estimates
        if _get(e, "error") is None and _to_float(_get(e, "point")) is not None
    ]
    if not usable:
        return {"status": "empty", "label": _LABELS["empty"], "avg": None, "n_usable": 0}

    points = [_to_float(_get(e, "point")) for e in usable]
    avg = sum(points) / len(points)

    if abs(avg) < MATERIAL_THRESHOLD:
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
        else:
            status = "detected"

    return {"status": status, "label": _LABELS[status], "avg": avg, "n_usable": len(usable)}
