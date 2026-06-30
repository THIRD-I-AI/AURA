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
from shared.sql_identifiers import quote_identifier

_DEFAULT_ROW_LIMIT = 1000
_DATA_CARD_SAMPLES = 5


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


def _list_tables_handler(arguments: Dict[str, Any], *, tenant: str, con: Any) -> ToolOutcome:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    return ToolOutcome(ok=True, value={"tables": [r[0] for r in rows]})


def _describe_table_handler(arguments: Dict[str, Any], *, tenant: str, con: Any) -> ToolOutcome:
    table = str(arguments.get("table", "")).strip()
    if not table:
        return ToolOutcome(ok=False, error="table must be a non-empty string")
    # Parameterized: the model-supplied name is a VALUE in the WHERE clause,
    # never interpolated into SQL — no identifier injection is possible.
    rows = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return ToolOutcome(ok=True, value={
        "table": table,
        "columns": [{"name": r[0], "type": r[1]} for r in rows],
    })


def _data_card_handler(arguments: Dict[str, Any], *, tenant: str, con: Any) -> ToolOutcome:
    """Compact, AI-native summary of one table: row count + per-column type,
    cardinality, null fraction, and a few sample values — enough for the model
    to reason about each column's role without scanning the raw data."""
    table = str(arguments.get("table", "")).strip()
    if not table:
        return ToolOutcome(ok=False, error="table must be a non-empty string")
    # Validate existence + get the REAL column names via a parameterized query;
    # the model-supplied table name is a bound value here, never interpolated.
    cols = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    if not cols:
        return ToolOutcome(ok=False, error=f"table '{table}' not found")

    # The table exists, so its name and (DB-sourced) column names are now safe
    # to quote and interpolate for the stats queries.
    qtable = quote_identifier(table)
    row_count = con.execute(f"SELECT count(*) FROM {qtable}").fetchone()[0]
    card_cols: List[Dict[str, Any]] = []
    for name, dtype in cols:
        qcol = quote_identifier(name)
        ndistinct, nnull = con.execute(
            f"SELECT count(DISTINCT {qcol}), count(*) - count({qcol}) FROM {qtable}"
        ).fetchone()
        samples = [r[0] for r in con.execute(
            f"SELECT DISTINCT {qcol} FROM {qtable} WHERE {qcol} IS NOT NULL LIMIT {_DATA_CARD_SAMPLES}"
        ).fetchall()]
        card_cols.append({
            "name": name, "type": dtype, "distinct": ndistinct,
            "null_frac": round(nnull / row_count, 3) if row_count else 0.0,
            "samples": samples,
        })
    return ToolOutcome(ok=True, value={"table": table, "row_count": row_count, "columns": card_cols})


_GET_DATA_CARD = CommanderTool(
    name="get_data_card",
    description="Get a compact semantic profile of one table — per-column type, "
                "distinct-count, null fraction, and sample values. Use it to "
                "understand what each column means before writing run_sql.",
    parameters={
        "type": "object",
        "properties": {"table": {"type": "string", "description": "exact table name"}},
        "required": ["table"],
    },
    handler=_data_card_handler,
)


_LIST_TABLES = CommanderTool(
    name="list_tables",
    description="List the names of all datasets/tables the user has loaded. "
                "Call this first to discover what data is available.",
    parameters={"type": "object", "properties": {}},
    handler=_list_tables_handler,
)

_DESCRIBE_TABLE = CommanderTool(
    name="describe_table",
    description="Return the column names and types of one table. Call this for "
                "the table(s) you need BEFORE writing run_sql — never guess column names.",
    parameters={
        "type": "object",
        "properties": {"table": {"type": "string", "description": "exact table name"}},
        "required": ["table"],
    },
    handler=_describe_table_handler,
)


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
    reg.register(_LIST_TABLES)
    reg.register(_DESCRIBE_TABLE)
    reg.register(_GET_DATA_CARD)
    reg.register(_RUN_SQL)
    return reg
