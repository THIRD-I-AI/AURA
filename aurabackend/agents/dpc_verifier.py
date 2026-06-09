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


import ast
import re
import threading

# Only these names may appear in a DPC pandas expression. `pd`/`np` are
# deliberately NOT exposed: pd.read_pickle / pd.read_sql etc. are RCE / I/O
# sinks, and the value cross-check never needs module-level functions.
_SAFE_BUILTINS = {
    "len": len, "abs": abs, "round": round, "min": min, "max": max,
    "sum": sum, "sorted": sorted, "float": float, "int": int, "bool": bool,
}
_ALLOWED_NAMES = frozenset({"df", *_SAFE_BUILTINS})
# Attribute names that read/write external state or reach private internals.
_BAD_ATTR_PREFIXES = ("_", "to_", "read_")
_BAD_ATTRS = frozenset({"eval", "query", "pipe"})
_BANNED_NODES = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                 ast.GeneratorExp, ast.Await, ast.Yield, ast.YieldFrom,
                 ast.NamedExpr, ast.Starred)


def _assert_safe_expr(expr: str) -> "ast.Expression":
    """Statically validate that `expr` is a safe pandas value expression over
    `df`, BEFORE any evaluation. Rejects imports/statements, lambdas and
    comprehensions (which bind new names), private or I/O attributes (`__*`,
    `to_*`, `read_*`, eval/query/pipe), and any name other than `df` + a few
    safe builtins. This is the primary defense — a regex denylist let module
    sinks like `pd.read_pickle(...)` through."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"unparsable pandas expression: {exc}")
    for node in ast.walk(tree):
        if isinstance(node, _BANNED_NODES):
            raise ValueError(f"disallowed construct: {type(node).__name__}")
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith(_BAD_ATTR_PREFIXES) or node.attr in _BAD_ATTRS
        ):
            raise ValueError(f"disallowed attribute: {node.attr}")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise ValueError(f"disallowed name: {node.id}")
    return tree


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
    """Evaluate ONE pandas expression over `df` in a locked-down sandbox.

    Defense in depth: a static AST allowlist (`_assert_safe_expr`: only `df` +
    a few safe builtins as names; no imports/lambdas/comprehensions; no
    private/`to_`/`read_`/eval/query/pipe attributes) BEFORE evaluation, then
    eval with `__builtins__` replaced by a tiny safe set and only `df` in scope,
    under a wall-clock timeout. `pd`/`np` are intentionally absent so module
    sinks like `pd.read_pickle` / `pd.read_sql` are unreachable. Raises
    ValueError on a policy violation; the underlying error / TimeoutError
    otherwise."""
    code = compile(_assert_safe_expr(expr), "<dpc-pandas>", "eval")
    safe_globals = {"__builtins__": _SAFE_BUILTINS}

    def _do():
        # Sandboxed eval — the core of the approved DPC design (S32 spec):
        # cross-check the SQL answer against an LLM-written pandas expression.
        # The expression is AST-allowlisted above (df + safe builtins only, no
        # module access, no I/O attributes); here builtins are the safe set and
        # only `df` is in scope. ast.literal_eval can't run pandas method chains.
        return eval(code, safe_globals, {"df": df})  # nosec B307

    return _run_with_timeout(_do, timeout_s)


def extract_columns_rows(res: Any) -> Tuple[List[str], Optional[List[List[Any]]]]:
    """Pull (columns, rows) out of an execute_sql result (dict or model)."""
    if isinstance(res, dict):
        return (res.get("columns") or []), res.get("rows")
    return (getattr(res, "columns", None) or []), getattr(res, "rows", None)


async def materialize_table(table: str, tools: Any, max_rows: int) -> Optional[pd.DataFrame]:
    """Fetch the whole table via execute_sql → DataFrame, or None if too big / unreadable.

    `table` is the sqlglot-extracted identifier (never raw user text); it is
    re-quoted with double quotes for the SELECT.
    """
    if tools is None:
        return None
    try:
        res = await tools.call("execute_sql", query=f'SELECT * FROM "{table}"')
    except Exception:
        return None
    cols, rows = extract_columns_rows(res)
    if rows is None:
        return None
    if len(rows) > max_rows:
        return None
    try:
        return pd.DataFrame(rows, columns=cols)
    except Exception:
        return None


_PANDAS_PROMPT = """\
You are a data analyst. A pandas DataFrame named `df` has these columns and dtypes:
{schema}

Sample rows:
{sample}

Write a SINGLE pandas EXPRESSION (no statements, no imports, no assignment, no
print) over `df` that computes the answer to this question:
{question}

Rules:
- Use ONLY `df` and the builtins len/sum/min/max/abs/round. Do NOT use the
  pandas (`pd`) or numpy (`np`) modules, lambdas, or comprehensions.
- Reference columns by subscript, e.g. df["col"].
Return ONLY the expression, for example: df[df["x"] > 0]["y"].sum()
No markdown, no backticks, no explanation."""


def _strip_expr(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    text = text.strip()
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def generate_pandas_solution(question: str, df: pd.DataFrame, llm: Any) -> str:
    """Ask the LLM for ONE pandas expression over `df`. Raises if unavailable/empty."""
    if not llm.is_available():
        raise RuntimeError("LLM unavailable for pandas cross-check")
    schema = "\n".join(f'- "{c}": {df[c].dtype}' for c in df.columns)
    sample = df.head(3).to_string(index=False)
    text = llm.generate(_PANDAS_PROMPT.format(schema=schema, sample=sample, question=question))
    if not text:
        raise RuntimeError("LLM returned an empty pandas solution")
    expr = _strip_expr(text)
    if not expr:
        raise RuntimeError("no pandas expression parsed from LLM output")
    return expr
