# DPC Dual-Paradigm SQL Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cross-check `SQLGeneratorAgent`-generated SQL against an independently-generated Pandas solution (constrained eval) over the same data, and annotate the agent result with a tri-state `verified`/`mismatch`/`skipped` verdict.

**Architecture:** One new module `aurabackend/agents/dpc_verifier.py` with small pure-ish functions (table extraction, table materialization via the existing `execute_sql` tool, Pandas-solution generation via the LLM, sandboxed eval, value-multiset comparison, and a bounded orchestrator). `SQLGeneratorAgent._run` calls the orchestrator after it executes SQL; on `mismatch` it does one bounded SQL regeneration. The whole DPC pass is wall-clock-bounded so a slow LLM never blocks the answer (the critic-timeout lesson). Everything degrades to an honest `skipped` rather than a false "✓".

**Tech Stack:** Python 3.11/3.12, pandas, numpy, sqlglot (all existing base deps), pydantic v2, asyncio. Tests: pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-dpc-sql-verification-design.md`

---

## File Structure

- **Create** `aurabackend/agents/dpc_verifier.py` — all DPC logic. Public surface:
  `VerificationResult`, `extract_single_table`, `extract_columns_rows`, `materialize_table`,
  `generate_pandas_solution`, `safe_eval_pandas`, `results_agree`, `verify_sql_result`,
  and config readers `dpc_enabled`/`dpc_timeout`/`dpc_max_rows`/`dpc_max_retries`.
- **Modify** `aurabackend/agents/specialists/sql_generator_agent.py` — call DPC after execution
  (the post-execution block in `_run`, currently ending at the `result.output = {...}` assignment).
- **Create** `aurabackend/tests/test_dpc_verifier.py` — Tier A unit tests (no optional deps; LLM/tools faked).
- **Create** `aurabackend/tests/test_dpc_integration.py` — Tier A integration test of `SQLGeneratorAgent` with fakes.
- **Create** `aurabackend/tests/test_dpc_real_llm.py` — Tier B, `skipif` no LLM configured; runs on the real-LLM eval-gate lane.

All test files are collected by the base Backend Tests lane (pandas/numpy/sqlglot are base deps); the Tier-B file self-skips unless a provider is configured.

---

### Task 1: `VerificationResult` model + `results_agree` comparison

**Files:**
- Create: `aurabackend/agents/dpc_verifier.py`
- Test: `aurabackend/tests/test_dpc_verifier.py`

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_dpc_verifier.py
"""DPC dual-paradigm SQL verification — Tier A unit tests (LLM/tools faked)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.dpc_verifier import VerificationResult, results_agree


def test_verification_result_defaults():
    vr = VerificationResult(status="skipped", verified=None, reason="multi-table")
    assert vr.status == "skipped"
    assert vr.verified is None
    assert vr.method == "dual_paradigm_pandas"
    assert vr.pandas_expr is None


def test_results_agree_scalar_match():
    # SQL returned one cell = 6; pandas computed scalar 6 → agree.
    assert results_agree(["s"], [[6]], 6) is True


def test_results_agree_scalar_mismatch():
    assert results_agree(["s"], [[5]], 6) is False


def test_results_agree_within_tolerance():
    assert results_agree(["s"], [[1.0000001]], 1.0, tol=1e-6) is True


def test_results_agree_order_insensitive():
    # Row order differs between SQL and pandas — still the same value set.
    pandas_side = pd.DataFrame({"g": ["b", "a"], "v": [3, 1]})
    assert results_agree(["g", "v"], [["a", 1], ["b", 3]], pandas_side) is True


def test_results_agree_series_and_none():
    s = pd.Series([10, 20])
    assert results_agree(["c"], [[10], [20]], s) is True
    assert results_agree(["c"], [[None]], pd.Series([np.nan])) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.dpc_verifier'`

- [ ] **Step 3: Write minimal implementation**

```python
# aurabackend/agents/dpc_verifier.py
"""DPC — Dual-Paradigm SQL Verification.

After SQLGeneratorAgent runs SQL, independently re-derive the answer in pandas
and cross-check. Tri-state verdict; bounded; degrades to an honest `skipped`.
See docs/superpowers/specs/2026-05-31-dpc-sql-verification-design.md.
"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel
from typing_extensions import Literal


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
    if isinstance(v, (bool,)):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/agents/dpc_verifier.py aurabackend/tests/test_dpc_verifier.py
git commit -m "feat(s32): VerificationResult + value-multiset results_agree"
```

---

### Task 2: `extract_single_table` (CTE-aware)

**Files:**
- Modify: `aurabackend/agents/dpc_verifier.py`
- Test: `aurabackend/tests/test_dpc_verifier.py`

- [ ] **Step 1: Write the failing test** (append to `test_dpc_verifier.py`)

```python
from agents.dpc_verifier import extract_single_table


def test_extract_single_table_simple():
    assert extract_single_table('SELECT * FROM "sales"') == "sales"


def test_extract_single_table_join_returns_none():
    sql = 'SELECT a.x FROM "t1" a JOIN "t2" b ON a.id = b.id'
    assert extract_single_table(sql) is None


def test_extract_single_table_cte_excludes_alias():
    # CTE alias `c` is not a base table — the lone base table is `orders`.
    sql = 'WITH c AS (SELECT * FROM "orders") SELECT count(*) FROM c'
    assert extract_single_table(sql) == "orders"


def test_extract_single_table_no_table():
    assert extract_single_table("SELECT 1") is None


def test_extract_single_table_unparseable():
    assert extract_single_table("this is not sql ;;;") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -k extract_single_table -v`
Expected: FAIL — `ImportError: cannot import name 'extract_single_table'`

- [ ] **Step 3: Write minimal implementation** (append to `dpc_verifier.py`)

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -k extract_single_table -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/agents/dpc_verifier.py aurabackend/tests/test_dpc_verifier.py
git commit -m "feat(s32): CTE-aware single-table extraction"
```

---

### Task 3: `safe_eval_pandas` (word-boundary denylist + no-builtins + timeout)

**Files:**
- Modify: `aurabackend/agents/dpc_verifier.py`
- Test: `aurabackend/tests/test_dpc_verifier.py`

- [ ] **Step 1: Write the failing test** (append to `test_dpc_verifier.py`)

```python
import time as _time

from agents.dpc_verifier import _run_with_timeout, safe_eval_pandas


def _df():
    return pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 3], "cost": [10, 20, 30]})


def test_safe_eval_pandas_correct():
    assert safe_eval_pandas('df["v"].sum()', _df()) == 6


def test_safe_eval_pandas_allows_columns_containing_denied_substrings():
    # "cost" contains "os" — must NOT be rejected (word-boundary denylist).
    assert safe_eval_pandas('df["cost"].sum()', _df()) == 60


@pytest.mark.parametrize("expr", [
    '__import__("os").system("echo hi")',
    "import os",
    'open("/etc/passwd").read()',
    "df.__class__.__mro__",
    "globals()",
    "os.getcwd()",
])
def test_safe_eval_pandas_rejects_dangerous(expr):
    with pytest.raises(ValueError):
        safe_eval_pandas(expr, _df())


def test_run_with_timeout_raises():
    with pytest.raises(TimeoutError):
        _run_with_timeout(lambda: _time.sleep(2.0), 0.2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -k "safe_eval or run_with_timeout" -v`
Expected: FAIL — `ImportError: cannot import name 'safe_eval_pandas'`

- [ ] **Step 3: Write minimal implementation** (append to `dpc_verifier.py`)

```python
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
        return eval(expr, {"__builtins__": {}}, namespace)

    return _run_with_timeout(_do, timeout_s)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -k "safe_eval or run_with_timeout" -v`
Expected: PASS (9 tests incl. parametrized)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/agents/dpc_verifier.py aurabackend/tests/test_dpc_verifier.py
git commit -m "feat(s32): sandboxed pandas eval (word-boundary denylist + timeout)"
```

---

### Task 4: `extract_columns_rows` + `materialize_table` + `generate_pandas_solution`

**Files:**
- Modify: `aurabackend/agents/dpc_verifier.py`
- Test: `aurabackend/tests/test_dpc_verifier.py`

- [ ] **Step 1: Write the failing test** (append to `test_dpc_verifier.py`)

```python
import asyncio

from agents.dpc_verifier import (
    extract_columns_rows,
    generate_pandas_solution,
    materialize_table,
)
from agents.tool_registry import Tool, ToolRegistry


def _tools(result):
    reg = ToolRegistry()

    async def _exec(*, query, connection_id="default"):
        return result

    reg.register(Tool(name="execute_sql", description="x", category="sql", fn=_exec))
    return reg


class _FixedLLM:
    def __init__(self, text):
        self._text = text

    def is_available(self):
        return True

    def generate(self, prompt, **kw):
        return self._text


def test_extract_columns_rows_from_dict():
    cols, rows = extract_columns_rows({"columns": ["a"], "rows": [[1]]})
    assert cols == ["a"] and rows == [[1]]


def test_materialize_table_builds_dataframe():
    tbl = {"columns": ["g", "v"], "rows": [["a", 1], ["b", 2]]}
    df = asyncio.run(materialize_table("t", _tools(tbl), max_rows=1000))
    assert list(df.columns) == ["g", "v"]
    assert df["v"].sum() == 3


def test_materialize_table_too_large_returns_none():
    tbl = {"columns": ["v"], "rows": [[1], [2], [3]]}
    assert asyncio.run(materialize_table("t", _tools(tbl), max_rows=2)) is None


def test_generate_pandas_solution_strips_fence():
    df = pd.DataFrame({"v": [1, 2]})
    expr = generate_pandas_solution("total v", df, _FixedLLM('```python\ndf["v"].sum()\n```'))
    assert expr == 'df["v"].sum()'


def test_generate_pandas_solution_raises_when_unavailable():
    class _Down(_FixedLLM):
        def is_available(self):
            return False

    with pytest.raises(RuntimeError):
        generate_pandas_solution("q", pd.DataFrame({"v": [1]}), _Down(""))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -k "materialize or columns_rows or pandas_solution" -v`
Expected: FAIL — `ImportError: cannot import name 'extract_columns_rows'`

- [ ] **Step 3: Write minimal implementation** (append to `dpc_verifier.py`)

```python
_PANDAS_PROMPT = """\
You are a data analyst. A pandas DataFrame named `df` has these columns and dtypes:
{schema}

Sample rows:
{sample}

Write a SINGLE pandas EXPRESSION (no statements, no imports, no assignment, no
print) over `df` that computes the answer to this question:
{question}

Return ONLY the expression, for example: df[df["x"] > 0]["y"].sum()
No markdown, no backticks, no explanation."""


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -k "materialize or columns_rows or pandas_solution" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/agents/dpc_verifier.py aurabackend/tests/test_dpc_verifier.py
git commit -m "feat(s32): table materialization + pandas-solution generation"
```

---

### Task 5: `verify_sql_result` orchestrator + config readers

**Files:**
- Modify: `aurabackend/agents/dpc_verifier.py`
- Test: `aurabackend/tests/test_dpc_verifier.py`

- [ ] **Step 1: Write the failing test** (append to `test_dpc_verifier.py`)

```python
from agents.dpc_verifier import dpc_enabled, dpc_max_rows, dpc_timeout, verify_sql_result

_TABLE = {"columns": ["g", "v"], "rows": [["a", 1], ["a", 2], ["b", 3]]}


def _verify(sql, sql_cols, sql_rows, pandas_expr, table=_TABLE, max_rows=1000, timeout=5.0):
    return asyncio.run(verify_sql_result(
        "total v", sql, sql_cols, sql_rows, _tools(table), _FixedLLM(pandas_expr),
        timeout=timeout, max_rows=max_rows, tol=1e-6,
    ))


def test_verify_sql_result_verified():
    vr = _verify('SELECT SUM("v") AS s FROM "t"', ["s"], [[6]], 'df["v"].sum()')
    assert vr.status == "verified" and vr.verified is True
    assert vr.pandas_expr == 'df["v"].sum()'


def test_verify_sql_result_mismatch():
    # SQL says 5, pandas says 6 → mismatch.
    vr = _verify('SELECT SUM("v") AS s FROM "t"', ["s"], [[5]], 'df["v"].sum()')
    assert vr.status == "mismatch" and vr.verified is False


def test_verify_sql_result_skips_multi_table():
    vr = _verify('SELECT 1 FROM "t1" JOIN "t2" ON 1=1', ["s"], [[1]], 'df["v"].sum()')
    assert vr.status == "skipped" and vr.verified is None
    assert "multi-table" in vr.reason.lower() or "single" in vr.reason.lower()


def test_verify_sql_result_skips_oversized():
    vr = _verify('SELECT SUM("v") AS s FROM "t"', ["s"], [[6]], 'df["v"].sum()', max_rows=1)
    assert vr.status == "skipped" and "large" in vr.reason.lower()


def test_verify_sql_result_skips_on_bad_pandas():
    # Denylisted pandas expr → eval raises → skipped (never crashes the caller).
    vr = _verify('SELECT SUM("v") AS s FROM "t"', ["s"], [[6]], 'os.system("x")')
    assert vr.status == "skipped"


def test_config_readers_defaults(monkeypatch):
    for k in ("AURA_DPC_ENABLED", "AURA_DPC_TIMEOUT_S", "AURA_DPC_MAX_ROWS"):
        monkeypatch.delenv(k, raising=False)
    assert dpc_enabled() is True
    assert dpc_timeout() == 10.0
    assert dpc_max_rows() == 200000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -k "verify_sql_result or config_readers" -v`
Expected: FAIL — `ImportError: cannot import name 'verify_sql_result'`

- [ ] **Step 3: Write minimal implementation** (append to `dpc_verifier.py`)

```python
import asyncio
import os


def dpc_enabled() -> bool:
    return os.getenv("AURA_DPC_ENABLED", "1") != "0"


def dpc_timeout() -> float:
    return float(os.getenv("AURA_DPC_TIMEOUT_S", "10"))


def dpc_max_rows() -> int:
    return int(os.getenv("AURA_DPC_MAX_ROWS", "200000"))


def dpc_max_retries() -> int:
    return int(os.getenv("AURA_DPC_MAX_RETRIES", "1"))


def _skipped(reason: str) -> VerificationResult:
    return VerificationResult(status="skipped", verified=None, reason=reason)


async def _verify_inner(question, sql, sql_columns, sql_rows, tools, llm,
                        max_rows: int, tol: float) -> VerificationResult:
    table = extract_single_table(sql)
    if table is None:
        return _skipped("multi-table or unparseable query not yet cross-verified")
    df = await materialize_table(table, tools, max_rows)
    if df is None:
        return _skipped("dataset too large or unreadable to cross-verify")
    expr = await asyncio.to_thread(generate_pandas_solution, question, df, llm)
    result = await asyncio.to_thread(safe_eval_pandas, expr, df)
    agree = results_agree(sql_columns, sql_rows, result, tol)
    return VerificationResult(
        status="verified" if agree else "mismatch",
        verified=agree,
        reason=("independent pandas computation agrees"
                if agree else "independent pandas computation disagrees"),
        pandas_expr=expr,
    )


async def verify_sql_result(question, sql, sql_columns, sql_rows, tools, llm, *,
                            timeout: float, max_rows: int, tol: float = 1e-6) -> VerificationResult:
    """One bounded verification pass. Never raises — any failure → `skipped`."""
    try:
        return await asyncio.wait_for(
            _verify_inner(question, sql, sql_columns, sql_rows, tools, llm, max_rows, tol),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return _skipped(f"cross-check exceeded {timeout:g}s")
    except Exception as exc:
        return _skipped(f"cross-check error: {type(exc).__name__}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py -v`
Expected: PASS (all Tier A tests, ~30)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/agents/dpc_verifier.py aurabackend/tests/test_dpc_verifier.py
git commit -m "feat(s32): bounded verify_sql_result orchestrator + config readers"
```

---

### Task 6: Integrate DPC into `SQLGeneratorAgent._run` (one bounded retry)

**Files:**
- Modify: `aurabackend/agents/specialists/sql_generator_agent.py` (imports near line 23; post-execution block near lines 123-133)
- Test: `aurabackend/tests/test_dpc_integration.py`

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_dpc_integration.py
"""DPC integration into SQLGeneratorAgent — Tier A (LLM + tools faked)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import AgentContext, AgentResult
from agents.specialists.sql_generator_agent import SQLGeneratorAgent
from agents.tool_registry import Tool, ToolRegistry

_TABLE = {"columns": ["g", "v"], "rows": [["a", 1], ["a", 2], ["b", 3]]}


class _RoutingLLM:
    """Returns SQL for the generation prompt and a pandas expr for the DPC prompt."""
    def __init__(self, sql, pandas_expr):
        self._sql, self._pandas = sql, pandas_expr

    def is_available(self):
        return True

    def generate(self, prompt, **kw):
        if "pandas DataFrame named" in prompt:
            return self._pandas
        if "Explain the following SQL" in prompt:
            return "returns the sum of v"
        return self._sql


def _tools(answer_rows):
    reg = ToolRegistry()

    async def _exec(*, query, connection_id="default"):
        q = query.strip()
        if q.upper().startswith("EXPLAIN"):
            return {"columns": [], "rows": []}
        if "SELECT * FROM" in q.upper():
            return _TABLE
        return {"columns": ["s"], "rows": answer_rows}

    reg.register(Tool(name="execute_sql", description="x", category="sql", fn=_exec))
    return reg


def _agent(answer_rows, sql, pandas_expr):
    agent = SQLGeneratorAgent(tool_registry=_tools(answer_rows))
    agent._llm = agent.llm = _RoutingLLM(sql, pandas_expr)
    return agent


def _ctx():
    return AgentContext(user_prompt="total v", task_description="total v",
                        schema_context={}, metadata={"execute": True})


def test_dpc_verified_annotation():
    agent = _agent([[6]], 'SELECT SUM("v") AS s FROM "t"', 'df["v"].sum()')
    res = asyncio.run(agent._run(_ctx(), AgentResult()))
    assert res.output["cross_verified"] is True
    assert res.output["verification"]["status"] == "verified"


def test_dpc_mismatch_after_retry_exhausted(monkeypatch):
    monkeypatch.setenv("AURA_DPC_MAX_RETRIES", "1")
    # SQL always returns 5; pandas always computes 6 → mismatch survives the retry.
    agent = _agent([[5]], 'SELECT SUM("v") AS s FROM "t"', 'df["v"].sum()')
    res = asyncio.run(agent._run(_ctx(), AgentResult()))
    assert res.output["cross_verified"] is False
    assert res.output["verification"]["status"] == "mismatch"


def test_dpc_disabled_no_annotation(monkeypatch):
    monkeypatch.setenv("AURA_DPC_ENABLED", "0")
    agent = _agent([[6]], 'SELECT SUM("v") AS s FROM "t"', 'df["v"].sum()')
    res = asyncio.run(agent._run(_ctx(), AgentResult()))
    assert "verification" not in res.output
    assert "cross_verified" not in res.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_dpc_integration.py -v`
Expected: FAIL — `KeyError: 'cross_verified'` (no DPC block yet)

- [ ] **Step 3: Add the import** to `sql_generator_agent.py` (after the existing `from agents.params import ...` line, ~line 24)

```python
from agents.dpc_verifier import (
    dpc_enabled,
    dpc_max_retries,
    dpc_max_rows,
    dpc_timeout,
    extract_columns_rows,
    verify_sql_result,
)
```

- [ ] **Step 4: Insert the DPC block** in `_run`, replacing the current tail
`result.output = {...}` / `result.artifacts[...]` / `return result` (lines ~123-133) with:

```python
        result.output = {
            "sql": sql,
            "valid": valid,
            "executed": exec_result is not None,
            "result_preview": str(exec_result)[:500] if exec_result else None,
            "explanation": explanation,
        }
        result.artifacts["generated_sql"] = sql
        if explanation:
            result.artifacts["sql_explanation"] = explanation

        # DPC: cross-check the executed SQL against an independent pandas
        # computation. Best-effort + bounded — never blocks or breaks the answer.
        if exec_result is not None and dpc_enabled():
            sql, exec_result = await self._dpc_cross_check(
                ctx.task_description, sql, exec_result, schema_text, result,
            )

        return result
```

- [ ] **Step 5: Add the helper method** to `SQLGeneratorAgent` (after `_run`, before `_generate_sql`)

```python
    async def _dpc_cross_check(self, question, sql, exec_result, schema_text, result):
        """Annotate result.output with a tri-state DPC verdict; one bounded retry
        on mismatch. Returns the (possibly retried) (sql, exec_result)."""
        cols, rows = extract_columns_rows(exec_result)
        vr = await verify_sql_result(
            question, sql, cols, rows, self.tools, self._llm,
            timeout=dpc_timeout(), max_rows=dpc_max_rows(),
        )
        retries = dpc_max_retries()
        while vr.status == "mismatch" and retries > 0:
            retries -= 1
            hint = (f"{question}\n\n(Note: a previous SQL `{sql[:200]}` disagreed with an "
                    "independent computation — reconsider the aggregation, filter, or grouping.)")
            new_sql, _ = await self._generate_sql(hint, schema_text)
            if not new_sql:
                break
            new_sql = self._sanitise(new_sql)
            if not await self._validate_sql(new_sql, result):
                break
            try:
                new_exec = await self.tools.call("execute_sql", query=new_sql)
            except Exception:
                break
            n_cols, n_rows = extract_columns_rows(new_exec)
            vr = await verify_sql_result(
                question, new_sql, n_cols, n_rows, self.tools, self._llm,
                timeout=dpc_timeout(), max_rows=dpc_max_rows(),
            )
            sql, exec_result = new_sql, new_exec
            # The retried SQL replaces the original; drop the now-stale explanation.
            result.output["sql"] = sql
            result.output["result_preview"] = str(exec_result)[:500]
            result.output["explanation"] = None
            result.artifacts["generated_sql"] = sql
            if vr.status == "verified":
                break

        result.output["cross_verified"] = vr.verified
        result.output["verification"] = {
            "status": vr.status, "reason": vr.reason,
            "pandas_expr": vr.pandas_expr, "method": vr.method,
        }
        result.add_step(action="dpc_verification",
                        output_summary=f"{vr.status}: {vr.reason}")
        return sql, exec_result
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_dpc_integration.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the focused regression + lint**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py tests/test_dpc_integration.py tests/test_agents.py -q`
Expected: PASS (no regression in agent tests)
Run: `cd aurabackend && python -m ruff check --fix agents/dpc_verifier.py agents/specialists/sql_generator_agent.py tests/test_dpc_verifier.py tests/test_dpc_integration.py --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823`
Expected: no remaining errors

- [ ] **Step 8: Commit**

```bash
git add aurabackend/agents/specialists/sql_generator_agent.py aurabackend/tests/test_dpc_integration.py
git commit -m "feat(s32): wire DPC into SQLGeneratorAgent with one bounded retry"
```

---

### Task 7: Tier B real-LLM test + docs/registry close-out

**Files:**
- Create: `aurabackend/tests/test_dpc_real_llm.py`
- Modify: `docs/SPRINTS.md` (move S32 from In-flight to Completed on merge)

- [ ] **Step 1: Write the Tier B test** (self-skips unless an LLM is configured)

```python
# aurabackend/tests/test_dpc_real_llm.py
"""DPC Tier B — exercises a REAL LLM. Runs on the eval-gate real-LLM lane;
self-skips when no provider is configured."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.dpc_verifier import verify_sql_result
from agents.tool_registry import Tool, ToolRegistry


def _llm_or_skip():
    from shared.llm_provider import get_llm
    llm = get_llm(model="")
    if not llm.is_available():
        pytest.skip("no LLM provider configured")
    return llm


_TABLE = {"columns": ["region", "amount"],
          "rows": [["west", 100], ["west", 50], ["east", 200]]}


def _tools():
    reg = ToolRegistry()

    async def _exec(*, query, connection_id="default"):
        return _TABLE

    reg.register(Tool(name="execute_sql", description="x", category="sql", fn=_exec))
    return reg


def test_real_llm_verifies_correct_sql():
    llm = _llm_or_skip()
    vr = asyncio.run(verify_sql_result(
        "total amount across all regions",
        'SELECT SUM("amount") AS total FROM "sales"', ["total"], [[350]],
        _tools(), llm, timeout=30.0, max_rows=10000,
    ))
    assert vr.status in ("verified", "skipped")  # skipped only if the LLM errors/times out
    if vr.status == "verified":
        assert vr.verified is True


def test_real_llm_catches_wrong_sql():
    llm = _llm_or_skip()
    # SQL claims the total is 999 — an independent pandas sum should disagree.
    vr = asyncio.run(verify_sql_result(
        "total amount across all regions",
        'SELECT SUM("amount") AS total FROM "sales"', ["total"], [[999]],
        _tools(), llm, timeout=30.0, max_rows=10000,
    ))
    assert vr.status in ("mismatch", "skipped")
    if vr.status == "mismatch":
        assert vr.verified is False
```

- [ ] **Step 2: Run it locally** (skips if no provider)

Run: `cd aurabackend && python -m pytest tests/test_dpc_real_llm.py -v`
Expected: SKIPPED (no provider locally) or PASS (with a provider)

- [ ] **Step 3: Confirm the full Tier A suite is green**

Run: `cd aurabackend && python -m pytest tests/test_dpc_verifier.py tests/test_dpc_integration.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add aurabackend/tests/test_dpc_real_llm.py
git commit -m "test(s32): Tier B real-LLM DPC cross-check (catches a wrong total)"
```

- [ ] **Step 5: Push the branch + open the PR** (do NOT move the SPRINTS row to Completed until the PR merges)

```bash
git push -u origin feature/s32-dpc-sql-verification
gh pr create --title "S32: DPC dual-paradigm SQL verification" \
  --body "Closes #46. Cross-checks SQLGeneratorAgent SQL against an independently-generated pandas solution (constrained eval); tri-state verified/mismatch/skipped; one bounded retry on mismatch; bounded so it never blocks the answer. Single-table + bounded-size scope for v1. Spec: docs/superpowers/specs/2026-05-31-dpc-sql-verification-design.md"
```

---

## Self-Review

**1. Spec coverage:**
- Tri-state `VerificationResult` → Task 1. ✓
- Single-table scope (CTE-aware) → Task 2. ✓
- Bounded-size materialization → Task 4 (`materialize_table` + `max_rows`). ✓
- Constrained pandas eval (denylist + no-builtins + timeout) → Task 3. ✓
- Pandas-solution generation → Task 4. ✓
- Value-multiset comparison → Task 1 (`results_agree`). ✓
- Bounded orchestrator + skip-on-any-failure → Task 5. ✓
- Config (`AURA_DPC_ENABLED/TIMEOUT_S/MAX_ROWS/MAX_RETRIES`) → Task 5 + used in Task 6. ✓
- Integration + one bounded retry + honest annotation → Task 6. ✓
- `AURA_DPC_ENABLED=0` ⇒ byte-identical behaviour → Task 6 test `test_dpc_disabled_no_annotation`. ✓
- Tier A + Tier B testing → Tasks 1-6 (A), Task 7 (B). ✓
- Security (word-boundary denylist, parsed-identifier materialization) → Task 3 + Task 4. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**3. Type consistency:** `VerificationResult(status, verified, reason, pandas_expr, method)` used identically in Tasks 1/5/6. `extract_columns_rows` returns `(cols, rows)` everywhere. `verify_sql_result(question, sql, sql_columns, sql_rows, tools, llm, *, timeout, max_rows, tol)` keyword-only signature matches all call sites (Task 5 tests, Task 6 helper, Task 7 tests). `results_agree(sql_columns, sql_rows, pandas_result, tol)` consistent. ✓
