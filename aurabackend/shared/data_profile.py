"""
Shared Data Profiler
====================
Lightweight column profiling used by VisualizationAgent (to pick a chart that
matches the data shape) and AnalysisAgent (to give the LLM the dtype context
its narrative needs). Pure-Python, no pandas/numpy dependency.

Profile vocabulary is intentionally aligned with `shared.data_utils`'s schema
context so the frontend, viz prompt, and analysis prompt all speak the same
type names: ``numeric | date | categorical | id | text``.
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Numeric helpers (moved from analysis_agent.py — re-exported there for
# backward compatibility)
# ---------------------------------------------------------------------------

def safe_float(v: Any) -> Optional[float]:
    """Return v as float, or None if it isn't numerically coercible."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def numeric_values(records: List[Dict[str, Any]], col: str) -> List[float]:
    return [f for r in records if (f := safe_float(r.get(col))) is not None]


def describe_column(values: List[float]) -> Dict[str, Any]:
    """Descriptive statistics for a numeric series."""
    n = len(values)
    if n == 0:
        return {}
    sorted_v = sorted(values)
    mean = statistics.mean(values)
    result: Dict[str, Any] = {
        "count": n,
        "mean": round(mean, 4),
        "min": sorted_v[0],
        "max": sorted_v[-1],
        "range": round(sorted_v[-1] - sorted_v[0], 4),
    }
    if n >= 2:
        result["std"] = round(statistics.stdev(values), 4)
        result["median"] = round(statistics.median(values), 4)
        result["p25"] = round(sorted_v[max(0, int(n * 0.25) - 1)], 4)
        result["p75"] = round(sorted_v[min(n - 1, int(n * 0.75))], 4)
    return result


# ---------------------------------------------------------------------------
# Column-type detection
# ---------------------------------------------------------------------------

_DATE_HINT_RE = re.compile(
    r"^\s*\d{4}[-/]\d{1,2}([-/]\d{1,2})?(\s|T|$)|^\s*\d{1,2}[-/]\d{1,2}[-/]\d{2,4}",
)
_DATE_NAME_RE = re.compile(r"date|time|year|month|day|quarter|week", re.IGNORECASE)
_ID_NAME_RE = re.compile(r"(^|_)id($|_)|uuid|guid", re.IGNORECASE)


def _looks_like_date(value: Any) -> bool:
    if isinstance(value, (date, datetime)):
        return True
    if isinstance(value, str):
        return bool(_DATE_HINT_RE.match(value))
    return False


def classify_column(
    col: str,
    samples: List[Any],
    distinct_count: int,
    row_count: int,
) -> str:
    """Return one of: numeric | date | categorical | id | text."""
    non_null = [v for v in samples if v is not None and v != ""]
    if not non_null:
        return "text"

    # Date detection wins over numeric when name hints + format match
    name_says_date = bool(_DATE_NAME_RE.search(col))
    if name_says_date and any(_looks_like_date(v) for v in non_null[:8]):
        return "date"
    if all(_looks_like_date(v) for v in non_null[:5]):
        return "date"

    # ID columns: name hint + high cardinality
    if _ID_NAME_RE.search(col) and distinct_count >= max(10, row_count * 0.9):
        return "id"

    # Numeric: every observed sample coerces to float
    if all(safe_float(v) is not None for v in non_null[:20]):
        return "numeric"

    # Categorical: low cardinality strings
    if distinct_count <= max(20, row_count * 0.5):
        return "categorical"

    return "text"


# ---------------------------------------------------------------------------
# Full profiler
# ---------------------------------------------------------------------------

def profile_columns(
    records: List[Dict[str, Any]],
    columns: Optional[List[str]] = None,
    sample_size: int = 200,
) -> Dict[str, Dict[str, Any]]:
    """
    Build a per-column profile suitable for LLM consumption.

    Returns a mapping ``{col_name: {dtype, distinct, null_ratio, sample, stats?}}``.
    Stats are only present for numeric columns.
    """
    if not records:
        return {}

    cols = columns or list(records[0].keys())
    sample = records[:sample_size]
    n = len(sample)
    profiles: Dict[str, Dict[str, Any]] = {}

    for col in cols:
        values = [r.get(col) for r in sample]
        non_null = [v for v in values if v is not None and v != ""]
        distinct = len({str(v) for v in non_null}) if non_null else 0

        dtype = classify_column(col, non_null, distinct, n)

        prof: Dict[str, Any] = {
            "dtype": dtype,
            "distinct": distinct,
            "null_ratio": round(1 - len(non_null) / n, 3) if n else 1.0,
            "sample": [str(v) for v in non_null[:3]],
        }

        if dtype == "numeric":
            prof["stats"] = describe_column(numeric_values(records, col))

        profiles[col] = prof

    return profiles


def profile_to_text(profiles: Dict[str, Dict[str, Any]]) -> str:
    """Compact, LLM-friendly one-line-per-column summary."""
    if not profiles:
        return "(no columns)"
    lines = []
    for col, p in profiles.items():
        bits = [f"{p['dtype']}", f"distinct={p['distinct']}"]
        if p["null_ratio"]:
            bits.append(f"nulls={p['null_ratio']}")
        if "stats" in p and p["stats"]:
            s = p["stats"]
            bits.append(f"min={s.get('min')}")
            bits.append(f"max={s.get('max')}")
            if "mean" in s:
                bits.append(f"mean={s['mean']}")
        if p["sample"]:
            bits.append(f"sample={p['sample']}")
        lines.append(f"  {col}: {', '.join(str(b) for b in bits)}")
    return "\n".join(lines)
