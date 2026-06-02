"""DPC — Dual-Paradigm SQL Verification.

After SQLGeneratorAgent runs SQL, independently re-derive the answer in pandas
and cross-check. Tri-state verdict; bounded; degrades to an honest `skipped`.
See docs/superpowers/specs/2026-05-31-dpc-sql-verification-design.md.
"""
from __future__ import annotations

import math
from typing import Any, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel


class VerificationResult(BaseModel):
    status: Literal["verified", "mismatch", "skipped"]
    verified: Optional[bool]
    reason: str
    pandas_expr: Optional[str] = None
    method: str = "dual_paradigm_pandas"


def _norm_scalar(v: Any) -> Tuple[str, Any]:
    """Normalise a cell to a comparable (type-tag, value) pair."""
    if v is None:
        return ("none", "")
    if isinstance(v, bool):
        return ("num", float(v))
    try:
        f = float(v)
        if math.isnan(f):
            return ("none", "")
        return ("num", round(f, 6))
    except (TypeError, ValueError):
        return ("str", str(v))


def _to_multiset(values_2d: Any, ndigits: int) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    for row in values_2d:
        cells = row if isinstance(row, (list, tuple, np.ndarray)) else [row]
        for v in cells:
            tag, val = _norm_scalar(v)
            if tag == "num":
                val = round(float(val), ndigits)
            out.append((tag, val))
    return sorted(out, key=lambda t: (t[0], str(t[1])))


def _pandas_to_2d(result: Any) -> List[List[Any]]:
    if isinstance(result, pd.DataFrame):
        return result.values.tolist()
    if isinstance(result, pd.Series):
        return [[v] for v in result.tolist()]
    if isinstance(result, np.ndarray):
        arr = result.tolist()
        return arr if (arr and isinstance(arr[0], list)) else [[v] for v in arr]
    return [[result]]


def results_agree(sql_columns: List[str], sql_rows: List[List[Any]],
                  pandas_result: Any, tol: float = 1e-6) -> bool:
    """Order- and label-insensitive value-multiset comparison with float tolerance."""
    ndigits = max(0, round(-math.log10(tol))) if tol > 0 else 6
    left = _to_multiset(sql_rows or [], ndigits)
    right = _to_multiset(_pandas_to_2d(pandas_result), ndigits)
    return left == right
