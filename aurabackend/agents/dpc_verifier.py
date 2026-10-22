"""DPC — Dual-Paradigm SQL Verification.

After SQLGeneratorAgent runs SQL, independently re-derive the answer in pandas
and cross-check. Tri-state verdict; bounded; degrades to an honest `skipped`.
See docs/superpowers/specs/2026-05-31-dpc-sql-verification-design.md.
"""
from __future__ import annotations

import asyncio
import datetime
import math
import os
import sys
import time
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
    if isinstance(v, datetime.datetime):
        # The SQL side arrives already serialised with isoformat() ('T' separator);
        # str() of a pandas Timestamp uses a space, so equal instants compared unequal.
        return ("str", v.isoformat())
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
    # BUG-435: a grouped result keeps its group keys in the index, while the SQL
    # result has them as a column, so the two sides never held the same cells and a
    # correct GROUP BY answer was reported as a mismatch. A named index is data; the
    # unnamed positional index of a plain or filtered frame is not.
    if isinstance(result, (pd.DataFrame, pd.Series)) and all(n is not None for n in result.index.names):
        try:
            result = result.reset_index()
        except ValueError:  # an index level named like an existing column
            pass
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
# plot/hist/boxplot take backend="<module>" and import it (BUG-472 review).
_BAD_ATTRS = frozenset({"eval", "query", "pipe", "plot", "hist", "boxplot", "style"})
# BUG-479: methods whose output size is set by an argument rather than by the data
# (df["a"].repeat(10**9) or sample(n=..., replace=True) from a 2-row frame) or that
# multiply row counts (cross merge, explode). A value cross-check never needs them.
_AMPLIFYING_ATTRS = frozenset({
    "repeat", "sample", "merge", "join", "explode", "reindex", "reindex_like",
    "resize", "tile", "outer", "kron", "broadcast_to", "combine", "expanding",
    # BUG-506: string methods whose output length is an argument -- str.pad(10**8)
    # over a 2-row frame builds 200 MB -- and str.format/expandtabs, whose width
    # or tab size does the same from a constant ("{:>100000000}".format(1)).
    "pad", "ljust", "rjust", "center", "zfill", "format", "format_map", "expandtabs",
})
# BUG-479: the largest literal a sequence or string may be multiplied by, and the
# largest constant exponent ("x" * 600000000 and 10**300000000 pass the name and
# attribute checks; the big int is computed holding the GIL, so no timeout fires).
_MAX_LITERAL_FACTOR = 10_000
_MAX_EXPONENT = 64
_BANNED_NODES = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                 ast.GeneratorExp, ast.Await, ast.Yield, ast.YieldFrom,
                 ast.NamedExpr, ast.Starred)
# BUG-472: these methods take a method NAME as a string and getattr() it on the
# frame (pandas apply_str), so df.apply("to_csv", args=[path]) reached the I/O
# sinks the attribute check above blocks. A denylist of string constants is
# bypassable ("t" + "o_csv", a name read from the data), so the function spec of
# a dispatching call must be a literal from this allowlist or a safe builtin.
_STR_DISPATCH = frozenset({"apply", "agg", "aggregate", "transform", "map", "applymap", "pivot_table"})
_SAFE_STR_FUNCS = frozenset({
    "sum", "mean", "median", "min", "max", "count", "nunique", "size", "std",
    "var", "sem", "prod", "first", "last", "idxmin", "idxmax", "any", "all",
    "cumsum", "cumprod", "cummin", "cummax", "rank", "abs", "round", "skew",
    "kurt", "quantile", "shift", "diff", "pct_change", "unique", "mode",
    "value_counts", "describe",
})
_SPEC_KW = ("func", "arg", "aggfunc")


def _is_safe_func_spec(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and node.value in _SAFE_STR_FUNCS
    if isinstance(node, ast.Name):
        return node.id in _SAFE_BUILTINS
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_is_safe_func_spec(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        # Keys are column labels; the values are what pandas dispatches.
        return all(_is_safe_func_spec(v) for v in node.values)
    return False


def _is_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_is_constant(e) for e in node.elts)
    return False


def _is_named_aggregation(call: ast.Call) -> bool:
    """agg(out=("col", spec)) / Series.agg(out=spec), with no positional spec or a
    literal None. pandas reads every keyword but axis as a spec, so each one must
    be a (literal column, vetted spec) pair or a vetted spec."""
    if len(call.args) > 1 or (call.args and not (
            isinstance(call.args[0], ast.Constant) and call.args[0].value is None)):
        return False
    named = [kw for kw in call.keywords if kw.arg != "axis"]
    if not named or any(kw.arg is None or kw.arg in _SPEC_KW for kw in named):
        return False
    for kw in call.keywords:
        v = kw.value
        if kw.arg == "axis":
            if not isinstance(v, ast.Constant):
                return False
        elif isinstance(v, ast.Tuple):
            if not (len(v.elts) == 2 and isinstance(v.elts[0], ast.Constant)
                    and _is_safe_func_spec(v.elts[1])):
                return False
        elif not _is_safe_func_spec(v):
            return False
    return True


def _dispatch_call_is_safe(call: ast.Call) -> bool:
    """BUG-472: a dispatching call is safe only when it names exactly one function
    spec, that spec is vetted, and every other argument is a literal constant.

    Two earlier versions vetted "the spec" and skipped arguments believed not to
    be one; each was bypassed, because with func=None pandas reads *every*
    keyword as a named-aggregation spec (agg(None, x=("a", "to_csv")),
    agg(None, x=..., args="to_csv")). So named aggregation is allowed only when
    EVERY keyword is a vetted spec, and otherwise no argument besides the vetted
    spec may carry a non-literal value (a name read from the data)."""
    attr = call.func.attr  # type: ignore[attr-defined]
    if attr in ("agg", "aggregate") and _is_named_aggregation(call):
        return True
    spec_pos = 3 if attr == "pivot_table" else 0
    positional = list(call.args)
    specs = positional[spec_pos:spec_pos + 1]
    others = positional[:spec_pos] + positional[spec_pos + 1:]
    for kw in call.keywords:
        if kw.arg is None:
            return False  # **kwargs
        (specs if kw.arg in _SPEC_KW else others).append(kw.value)
    if attr == "pivot_table":
        if len(specs) > 1:
            return False
    elif len(specs) != 1:
        return False  # no spec means named aggregation (or a malformed call)
    if attr == "map":
        if not all(_is_safe_func_spec(sp) or _is_constant_lookup(sp) for sp in specs):
            return False
    elif not all(_is_safe_func_spec(sp) for sp in specs):
        return False
    return all(_is_constant(o) for o in others)


def _is_constant_lookup(node: ast.AST) -> bool:
    # BUG-472 (review): Series.map(dict) is a value lookup, not a string
    # dispatch, so a dict literal of constants is safe for map even when its
    # values are arbitrary strings like {1: "x"}.
    return (isinstance(node, ast.Dict)
            and all(isinstance(k, ast.Constant) for k in node.keys)
            and all(isinstance(v, ast.Constant) for v in node.values))


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
    called = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _STR_DISPATCH):
            called.add(id(node.func))
            if not _dispatch_call_is_safe(node):
                raise ValueError(f"disallowed function argument to {node.func.attr}")
    for node in ast.walk(tree):
        # A dispatcher passed as a value (max(..., key=df.agg)) would get its
        # method name from data rather than from a vetted literal.
        if isinstance(node, ast.Attribute) and node.attr in _STR_DISPATCH and id(node) not in called:
            raise ValueError(f"disallowed reference to {node.attr}")
        if isinstance(node, _BANNED_NODES):
            raise ValueError(f"disallowed construct: {type(node).__name__}")
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith(_BAD_ATTR_PREFIXES) or node.attr in _BAD_ATTRS
        ):
            raise ValueError(f"disallowed attribute: {node.attr}")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise ValueError(f"disallowed name: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in _AMPLIFYING_ATTRS:
            raise ValueError(f"disallowed size-amplifying method: {node.attr}")
        if isinstance(node, ast.BinOp):
            _assert_bounded_binop(node)
        # BUG-506: f"{x:>100000000}" and "%100000000d" % x size a string by a
        # width written in the format; a value cross-check never formats text.
        if isinstance(node, (ast.JoinedStr, ast.FormattedValue)):
            raise ValueError("disallowed construct: string formatting")
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
                and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)):
            raise ValueError("disallowed construct: string formatting")
    return tree


def _constant_value(node: ast.AST) -> Optional[Any]:
    """The value of a subtree built only from numeric literals, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _constant_value(node.operand)
        return None if v is None else (-v if isinstance(node.op, ast.USub) else v)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Pow)):
        left, right = _constant_value(node.left), _constant_value(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Pow):
            return left ** right  # exponent already bounded by _assert_bounded_binop
        return {ast.Add: left + right, ast.Sub: left - right, ast.Mult: left * right}[type(node.op)]
    return None


def _assert_bounded_binop(node: ast.BinOp) -> None:
    """BUG-479: refuse arithmetic whose result size is set by a literal."""
    if isinstance(node.op, (ast.LShift, ast.RShift)):
        raise ValueError("disallowed operator: shift")
    if isinstance(node.op, ast.Pow):
        exponent = _constant_value(node.right)
        if exponent is None or abs(exponent) > _MAX_EXPONENT:
            raise ValueError("exponent must be a literal no larger than 64")
        if any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow) for n in ast.walk(node.left)):
            raise ValueError("nested powers are not allowed")
    if isinstance(node.op, ast.Mult):
        for side in (node.left, node.right):
            v = _constant_value(side)
            # A float factor cannot repeat a string or list; an int one can.
            if isinstance(v, int) and abs(v) > _MAX_LITERAL_FACTOR and not isinstance(v, bool):
                raise ValueError(f"integer factor larger than {_MAX_LITERAL_FACTOR}")


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

    if _CAN_FORK:
        return _run_in_capped_child(_do, timeout_s)
    return _run_with_timeout(_do, timeout_s)


# BUG-479: a thread cannot be killed, so a blowup the static check misses (one sized
# by the data, e.g. df["g"] * df["a"].max()) kept allocating after the timeout and
# could take the single gateway worker down. Where fork exists (the Linux deploy),
# the expression runs in a child process with an address-space cap, and a child
# that overruns is killed. Elsewhere (Windows dev) the thread runner remains.
_CAN_FORK = hasattr(os, "fork") and sys.platform.startswith("linux")
# BUG-506: 512 MB of headroom was already the whole 512M gateway container.
_CHILD_EXTRA_BYTES = 256 * 1024 * 1024
# A value cross-check returns a scalar or a small table; anything larger is refused.
_MAX_RESULT_BYTES = 32 * 1024 * 1024


def _vm_size_bytes() -> int:
    with open("/proc/self/statm") as fh:
        return int(fh.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")


def _run_in_capped_child(fn, timeout_s: float):
    import pickle
    import resource
    import select
    import signal

    read_fd, write_fd = os.pipe()
    cap = _vm_size_bytes() + _CHILD_EXTRA_BYTES
    pid = os.fork()
    if pid == 0:  # child: compute, send one pickled reply, exit without cleanup
        status = 0
        try:
            os.close(read_fd)
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            try:
                payload = pickle.dumps(("ok", fn()))
            except MemoryError:
                payload = pickle.dumps(("err", MemoryError("pandas eval exceeded its memory cap")))
            except Exception as exc:
                try:
                    payload = pickle.dumps(("err", exc))
                except Exception:
                    payload = pickle.dumps(("err", RuntimeError(f"{type(exc).__name__}: {exc}")))
            view = memoryview(payload)
            while view:
                view = view[os.write(write_fd, view):]
        except BaseException:
            status = 1
        finally:
            os._exit(status)

    os.close(write_fd)
    chunks: List[bytes] = []
    received = 0
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([read_fd], [], [], remaining)[0]:
                os.kill(pid, signal.SIGKILL)
                raise TimeoutError(f"pandas eval exceeded {timeout_s:g}s")
            chunk = os.read(read_fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            # BUG-506: the address-space cap binds only the child. Joining and
            # unpickling an unbounded reply here would materialise it two or three
            # times over in the gateway itself, so an oversized reply is refused.
            if received > _MAX_RESULT_BYTES:
                os.kill(pid, signal.SIGKILL)
                raise MemoryError("pandas eval result exceeded its size limit")
    finally:
        os.close(read_fd)
        os.waitpid(pid, 0)
    if not chunks:
        raise RuntimeError("pandas eval process exited without a result")
    kind, value = pickle.loads(b"".join(chunks))  # nosec B301 -- written by our own child
    if kind == "err":
        raise value
    return value


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
        # BUG-360: bounded in the query. The size check below used to run only after
        # the whole table had been fetched (twice over, as tuples and lists), so a
        # one-row COUNT(*) over a 5M-row table materialised all 5M rows to skip itself.
        res = await tools.call("execute_sql", query=f'SELECT * FROM "{table}" LIMIT {int(max_rows) + 1}')
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


# ── Config ────────────────────────────────────────────────────────────────

def dpc_enabled() -> bool:
    return os.getenv("AURA_DPC_ENABLED", "1") != "0"


def dpc_timeout() -> float:
    return float(os.getenv("AURA_DPC_TIMEOUT_S", "10"))


def dpc_max_rows() -> int:
    return int(os.getenv("AURA_DPC_MAX_ROWS", "200000"))


def dpc_max_retries() -> int:
    return int(os.getenv("AURA_DPC_MAX_RETRIES", "1"))


# ── Bounded orchestrator ──────────────────────────────────────────────────

def _skipped(reason: str) -> VerificationResult:
    return VerificationResult(status="skipped", verified=None, reason=reason)


async def _verify_inner(question, sql, sql_columns, sql_rows, tools, llm, max_rows, tol):
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
