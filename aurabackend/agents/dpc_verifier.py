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


try:
    import sqlglot
    from sqlglot import exp as _exp
    _SQLGLOT = True
except ImportError:  # pragma: no cover
    sqlglot = None  # type: ignore[assignment]
    _exp = None  # type: ignore[assignment]
    _SQLGLOT = False


def extract_single_table(sql: str) -> Optional[str]:
    """Return the lone base table referenced by `sql`, or None for 0/>1/unparseable.

    CTE alias names are excluded — the generation prompt prefers CTEs, so a
    single-table query is commonly wrapped in a WITH clause.
    """
    if not _SQLGLOT:
        return None
    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:
        return None
    if tree is None:
        return None
    cte_aliases = {c.alias_or_name.lower() for c in tree.find_all(_exp.CTE)}
    tables = {
        t.name for t in tree.find_all(_exp.Table)
        if t.name and t.name.lower() not in cte_aliases
    }
    return next(iter(tables)) if len(tables) == 1 else None


import re
import threading

# Dangerous identifiers matched as WHOLE WORDS so column names that merely
# contain them (e.g. "cost" contains "os", "position" contains "os") are not
# rejected. Dunder access (`__`) is blocked outright — no legitimate pandas
# expression needs it, and it is the usual sandbox-escape vector.
_DENY_WORDS = re.compile(
    r"\b(import|exec|eval|compile|globals|locals|getattr|setattr|"
    r"breakpoint|subprocess|os|sys|open|input)\b"
)


def _run_with_timeout(fn, timeout_s: float):
    """Run `fn()` in a daemon thread; raise TimeoutError if it overruns.

    The abandoned thread cannot be force-killed, but a pandas expression over an
    already-bounded DataFrame will finish on its own; this just stops us waiting.
    """
    box: dict = {}

    def runner():
        try:
            box["r"] = fn()
        except Exception as exc:  # surface the eval error to the caller
            box["e"] = exc

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        raise TimeoutError(f"pandas eval exceeded {timeout_s:g}s")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def safe_eval_pandas(expr: str, df: pd.DataFrame, timeout_s: float = 5.0) -> Any:
    """Evaluate a single pandas expression over `df` in a locked-down namespace.

    Defense in depth: word-boundary denylist + `__` block, `__builtins__`
    stripped, only {df, pd, np} in scope, wall-clock timeout. Raises ValueError
    on a denylist hit, or the underlying error / TimeoutError otherwise.
    """
    if "__" in expr:
        raise ValueError("disallowed token in pandas expression: __")
    hit = _DENY_WORDS.search(expr)
    if hit:
        raise ValueError(f"disallowed token in pandas expression: {hit.group(0)}")
    namespace = {"df": df, "pd": pd, "np": np}

    def _do():
        # Deliberate, sandboxed eval — the core of the approved DPC dual-paradigm
        # design (S32 spec): cross-check the SQL answer against an LLM-written
        # pandas expression. Hardened above (denylist + `__` block) and here
        # (`__builtins__` stripped, only {df, pd, np} in scope, wall-clock
        # timeout). Threat model: non-adversarial LLM, local/single-tenant,
        # user's own data. ast.literal_eval can't run pandas method chains.
        return eval(expr, {"__builtins__": {}}, namespace)  # nosec B307

    return _run_with_timeout(_do, timeout_s)
