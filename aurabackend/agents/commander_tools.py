"""
Commander Tool Registry
=======================
The reactive commander loop (agents/commander.py) calls AURA capabilities
through this registry. Distinct from agents/tool_registry.py (async, agent
executor): commander tools are SYNC, receive the verified tenant + a
tenant-scoped DuckDB connection as injected kwargs, validate their arguments,
and NEVER raise to the loop — every failure is a ToolOutcome(ok=False).

run_sql is guarded two ways, deterministically and model-independently:
  1. a sqlglot parse that requires exactly ONE statement of SELECT/CTE *type*
     — this rejects DuckDB escapes a keyword blocklist misses (ATTACH, COPY,
     PRAGMA, INSTALL, LOAD) and multi-statement injection, structurally;
  2. the canonical SQLSafetyValidator (the same one the live /queries path
     uses) for defense-in-depth + the safety LIMIT.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from safety import SQLSafetyValidator

_DEFAULT_ROW_LIMIT = 1000


@dataclass
class ToolOutcome:
    ok: bool
    value: Any = None
    error: Optional[str] = None


@dataclass
class CommanderTool:
    name: str
    description: str
    parameters: Dict[str, Any]            # JSON schema
    handler: Callable[..., ToolOutcome]   # handler(arguments, *, tenant, con) -> ToolOutcome
    mutating: bool = False


class CommanderToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, CommanderTool] = {}

    def register(self, tool: CommanderTool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> List[Dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: Dict[str, Any], *, tenant: str, con: Any) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(ok=False, error=f"unknown tool '{name}'")
        args = arguments or {}
        missing = [k for k in tool.parameters.get("required", []) if k not in args]
        if missing:
            return ToolOutcome(ok=False, error=f"missing required argument(s): {', '.join(missing)}")
        try:
            return tool.handler(args, tenant=tenant, con=con)
        except Exception as exc:  # tools must never raise to the loop
            return ToolOutcome(ok=False, error=f"{name} failed: {exc}")


def _assert_select_only(sql: str) -> Tuple[bool, Optional[str]]:
    """(ok, error). Require exactly one SELECT/CTE statement by parsing it.

    Type-based, not keyword-based: rejects ATTACH/COPY/PRAGMA/INSTALL/LOAD and
    `;`-joined multi-statements that a keyword blocklist would miss. Falls back
    to a conservative prefix+single-statement check only when sqlglot is
    unavailable (air-gapped boxes without the optional dep)."""
    try:
        import sqlglot
        from sqlglot import expressions as exp
    except Exception:
        head = sql.lstrip().split(None, 1)[0].lower() if sql.strip() else ""
        if head not in {"select", "with"}:
            return False, "Only a single SELECT statement is permitted"
        if ";" in sql.strip().rstrip(";"):
            return False, "Only a single statement is permitted"
        return True, None
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:
        return False, f"could not parse SQL: {exc}"
    if len(statements) != 1:
        return False, "Only a single statement is permitted"
    stmt = statements[0]
    if stmt is None or not isinstance(stmt, (exp.Select, exp.Subquery, exp.With)):
        got = type(stmt).__name__ if stmt is not None else "empty"
        return False, f"Only SELECT statements are permitted (got {got})"
    return True, None


def _run_sql_handler(arguments: Dict[str, Any], *, tenant: str, con: Any) -> ToolOutcome:
    sql = str(arguments.get("sql", "")).strip()
    if not sql:
        return ToolOutcome(ok=False, error="sql must be a non-empty string")

    ok, err = _assert_select_only(sql)
    if not ok:
        return ToolOutcome(ok=False, error=err)

    validator = SQLSafetyValidator()
    result = validator.validate(sql)
    if not result.is_valid:
        return ToolOutcome(ok=False, error="; ".join(result.errors) or "rejected by SQL safety validator")

    safe_sql = sql if "LIMIT" in sql.upper() else validator.add_safety_limit(sql)
    cur = con.execute(safe_sql)
    columns = [d[0] for d in (cur.description or [])]
    rows = [list(r) for r in cur.fetchmany(_DEFAULT_ROW_LIMIT)]
    return ToolOutcome(ok=True, value={"columns": columns, "rows": rows, "row_count": len(rows)})


_RUN_SQL = CommanderTool(
    name="run_sql",
    description=(
        "Run a single read-only SQL SELECT against the user's loaded datasets and "
        "return rows. Use the exact table/column names from the schema context. "
        "SELECT only — no DDL/DML."
    ),
    parameters={
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "a single SELECT statement"}},
        "required": ["sql"],
    },
    handler=_run_sql_handler,
    mutating=False,
)


def build_default_registry() -> CommanderToolRegistry:
    reg = CommanderToolRegistry()
    reg.register(_RUN_SQL)
    return reg
