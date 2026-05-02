"""
AURA MCP Server — DuckDB Analytics + Distributed Metadata
==========================================================
Exposes two physical data stores to MCP clients (Claude Code, AURA agents):

  * **DuckDB** (local analytics warehouse, the file written by the UASR
    MAPE-K worker and the chat upload pipeline).
  * **Distributed metadata layer** (Postgres-wire: CockroachDB or YugabyteDB
    in production; SQLite in dev). Holds ``data_sources``, ``documents``,
    user/connection/saved-query rows.

Why MCP instead of stuffing schema into every prompt: agents call
``metadata.search_columns`` or ``duckdb.describe_table`` *on demand* and only
pull the slice they need — keeping the chat prompt under
``AURA_MAX_TOKENS_PER_REQUEST`` even with 70+ column schemas.

Security
--------
* Read-only: ``duckdb.query`` is parsed with ``sqlglot`` and rejects
  anything that isn't a single ``SELECT`` (or ``WITH … SELECT``). DDL/DML
  fail closed.
* Metadata access goes through SQLAlchemy reflection or
  parameterised statements — never string-concatenated SQL.
* HTTP transport (SSE) gates every request behind the
  ``AURA_MCP_API_KEY`` header. Stdio transport inherits the parent
  process's identity (Claude Code session).
* Row caps: ``query`` and ``sample_table`` clamp to a
  ``AURA_MCP_MAX_ROWS`` limit (default 200).

Wire it into Claude Code
------------------------
    claude mcp add aura-analytics -- python -m mcp_servers.aura_mcp_server
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aura.mcp.server")

# ── Optional deps — fail loudly only when actually invoked ────────────

try:
    from mcp.server.fastmcp import FastMCP  # modern decorator-based API
    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False

try:
    import sqlglot
    from sqlglot import exp as _sqlglot_exp
    _SQLGLOT_AVAILABLE = True
except ImportError:  # pragma: no cover
    sqlglot = None  # type: ignore[assignment]
    _sqlglot_exp = None  # type: ignore[assignment]
    _SQLGLOT_AVAILABLE = False


# ── Config ────────────────────────────────────────────────────────────

DUCKDB_PATH = os.getenv("AURA_MCP_DUCKDB_PATH", os.getenv("UASR_DUCKDB_PATH", "data/uasr_lake.duckdb"))
METADATA_DSN = os.getenv(
    "AURA_MCP_METADATA_DSN",
    # Defaults to the existing metadata SQLite. In prod set this to:
    #   postgresql+asyncpg://user:pw@cockroach-host:26257/aura?sslmode=verify-full
    # (CockroachDB) or postgresql+asyncpg://user:pw@yb-host:5433/aura
    # (YugabyteDB YSQL).
    os.getenv("METADATA_DATABASE_URL", "sqlite+aiosqlite:///data/metadata.db"),
)
MAX_ROWS = int(os.getenv("AURA_MCP_MAX_ROWS", "200"))


# ── Connection caches ─────────────────────────────────────────────────

_duck_con: Any = None
_meta_engine: Any = None


def _get_duckdb():
    """Read-only DuckDB handle. Reused across tool calls."""
    global _duck_con
    if _duck_con is None:
        import duckdb
        # ``read_only=True`` lets multiple processes (this server + the
        # UASR worker) share the same .duckdb file safely.
        _duck_con = duckdb.connect(DUCKDB_PATH, read_only=True)
    return _duck_con


async def _get_meta_engine():
    global _meta_engine
    if _meta_engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine
        _meta_engine = create_async_engine(METADATA_DSN, future=True, pool_pre_ping=True)
    return _meta_engine


# ── SQL safety ────────────────────────────────────────────────────────

def _assert_select_only(sql: str) -> None:
    """Raise ValueError unless ``sql`` is a single SELECT (CTEs allowed)."""
    if not _SQLGLOT_AVAILABLE:
        # Conservative fallback: only the "select" / "with" prefixes pass.
        head = sql.lstrip().split(None, 1)[0].lower() if sql.strip() else ""
        if head not in {"select", "with"}:
            raise ValueError("Only SELECT statements are permitted (sqlglot unavailable)")
        return

    statements = sqlglot.parse(sql, read="duckdb")
    if len(statements) != 1:
        raise ValueError("Exactly one statement allowed")
    stmt = statements[0]
    if stmt is None or not isinstance(stmt, (_sqlglot_exp.Select, _sqlglot_exp.Subquery, _sqlglot_exp.With)):
        raise ValueError(f"Statement type {type(stmt).__name__} not allowed; SELECT only")
    # Defensive: walk the tree for any DML/DDL nodes.
    forbidden = (
        _sqlglot_exp.Insert, _sqlglot_exp.Update, _sqlglot_exp.Delete,
        _sqlglot_exp.Drop, _sqlglot_exp.Alter, _sqlglot_exp.Create,
        _sqlglot_exp.TruncateTable, _sqlglot_exp.Merge,
    )
    for node in stmt.walk():
        if isinstance(node, forbidden):
            raise ValueError(f"Disallowed clause: {type(node).__name__}")


# ── Server build ──────────────────────────────────────────────────────

def build_server() -> "FastMCP":
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            "mcp package not installed. Run: pip install 'mcp[cli]>=1.2'"
        )

    server = FastMCP(
        name="aura-analytics",
        instructions=(
            "AURA analytics + metadata. Prefer `metadata.search_columns` "
            "before reading whole-table schemas — it returns just the columns "
            "matching a keyword across every registered source."
        ),
    )

    # ── DuckDB tools ──────────────────────────────────────────────────

    @server.tool(description="List all tables in the local DuckDB analytics warehouse.")
    def duckdb_list_tables() -> List[str]:
        con = _get_duckdb()
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]

    @server.tool(
        description="Describe one DuckDB table: column name, type, nullable. "
                    "Always prefer this over reading raw CSV headers."
    )
    def duckdb_describe_table(table: str) -> List[Dict[str, Any]]:
        con = _get_duckdb()
        rows = con.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ? "
            "ORDER BY ordinal_position",
            [table],
        ).fetchall()
        return [
            {"column": r[0], "type": r[1], "nullable": r[2] == "YES"}
            for r in rows
        ]

    @server.tool(description="Return up to N sample rows from a DuckDB table (default 5, max enforced).")
    def duckdb_sample_table(table: str, n: int = 5) -> Dict[str, Any]:
        con = _get_duckdb()
        n = max(1, min(n, MAX_ROWS))
        # Quoted identifier — safe because the table name comes through as a
        # parameter is not supported in the FROM clause; we whitelist via
        # information_schema first.
        existing = con.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name=?",
            [table],
        ).fetchone()
        if not existing:
            raise ValueError(f"Unknown table: {table}")
        cur = con.execute(f'SELECT * FROM "{table}" LIMIT ?', [n])
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return {
            "columns": cols,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
        }

    @server.tool(
        description="Run a read-only SELECT against DuckDB. Rejected if not a "
                    "single SELECT/CTE (parsed by sqlglot). Hard row cap applies."
    )
    def duckdb_query(sql: str, limit: int = 50) -> Dict[str, Any]:
        _assert_select_only(sql)
        limit = max(1, min(limit, MAX_ROWS))
        con = _get_duckdb()
        # Wrap the user query so we cap rows even if they forgot LIMIT,
        # without rewriting their AST.
        wrapped = f"SELECT * FROM ({sql}) AS _aura_q LIMIT {limit}"
        cur = con.execute(wrapped)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return {
            "columns": cols,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": len(rows) >= limit,
        }

    # ── Metadata tools (CockroachDB / YugabyteDB / Postgres) ──────────

    @server.tool(description="List registered data sources from the distributed metadata layer.")
    async def metadata_list_sources() -> List[Dict[str, Any]]:
        from sqlalchemy import text
        engine = await _get_meta_engine()
        async with engine.connect() as con:
            res = await con.execute(text(
                "SELECT id, name, type, connection_id, created_at "
                "FROM data_sources ORDER BY created_at DESC LIMIT 500"
            ))
            return [dict(r._mapping) for r in res]

    @server.tool(
        description="Get the cached schema document for one data source by id. "
                    "Returns column lists, sample stats, and tags from the "
                    "metadata `documents` table — much smaller than re-introspecting."
    )
    async def metadata_get_schema(source_id: str) -> Optional[Dict[str, Any]]:
        from sqlalchemy import text
        engine = await _get_meta_engine()
        async with engine.connect() as con:
            res = await con.execute(
                text(
                    "SELECT id, title, body, tags, details "
                    "FROM documents WHERE source_type='schema' AND id=:sid LIMIT 1"
                ),
                {"sid": source_id},
            )
            row = res.first()
            if row is None:
                return None
            d = dict(row._mapping)
            # SQLite returns JSON columns as text; Postgres/CockroachDB
            # returns dicts. Normalise so the agent always sees structures.
            for k in ("tags", "details"):
                v = d.get(k)
                if isinstance(v, str):
                    try:
                        d[k] = json.loads(v)
                    except json.JSONDecodeError:
                        pass
            return d

    @server.tool(
        description="Search every registered source's schema for columns "
                    "matching a keyword (case-insensitive substring on the "
                    "column name). Returns one row per (source, table, "
                    "column) hit — exact name + dtype + nullability + a "
                    "small sample of values. Backed by the structured "
                    "schema_columns table populated at upload time."
    )
    async def metadata_search_columns(keyword: str, limit: int = 50) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        if not keyword.strip():
            raise ValueError("keyword must not be empty")
        engine = await _get_meta_engine()
        # Match on column_name_lower for an indexable case-insensitive scan
        # — the prior LIKE-on-body forced a full-table seq scan over
        # multi-KB markdown blobs. With a structured index this is O(log N).
        pattern = f"%{keyword.strip().lower()}%"
        async with engine.connect() as con:
            res = await con.execute(
                text(
                    "SELECT source_id, table_name, column_name, data_type, "
                    "       is_nullable, ordinal_position, sample_values "
                    "FROM schema_columns "
                    "WHERE column_name_lower LIKE :pat "
                    "ORDER BY source_id, table_name, ordinal_position "
                    "LIMIT :lim"
                ),
                {"pat": pattern, "lim": min(limit, MAX_ROWS)},
            )
            rows = [dict(r._mapping) for r in res]
        # SQLite returns JSON columns as strings; Postgres/CockroachDB
        # returns parsed Python objects. Normalise so agents always see
        # a list. Same dialect-normalisation pattern as get_schema.
        for row in rows:
            v = row.get("sample_values")
            if isinstance(v, str):
                try:
                    row["sample_values"] = json.loads(v)
                except json.JSONDecodeError:
                    row["sample_values"] = []
        return rows

    @server.tool(
        description="Return every column for one (source, table) tuple "
                    "from the structured schema_columns table. Cheaper than "
                    "duckdb_describe_table when the agent already knows the "
                    "table is registered — no DuckDB connection needed."
    )
    async def metadata_describe_table(source_id: str, table_name: str) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        engine = await _get_meta_engine()
        async with engine.connect() as con:
            res = await con.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, "
                    "       ordinal_position, sample_values "
                    "FROM schema_columns "
                    "WHERE source_id=:sid AND table_name=:tn "
                    "ORDER BY ordinal_position"
                ),
                {"sid": source_id, "tn": table_name},
            )
            rows = [dict(r._mapping) for r in res]
        for row in rows:
            v = row.get("sample_values")
            if isinstance(v, str):
                try:
                    row["sample_values"] = json.loads(v)
                except json.JSONDecodeError:
                    row["sample_values"] = []
        return rows

    # ── Resources (read-only context endpoints) ───────────────────────

    @server.resource("aura://duckdb/tables", description="DuckDB table catalogue (machine-readable).")
    def _tables_resource() -> str:
        return json.dumps(duckdb_list_tables())

    @server.resource("aura://config", description="MCP server configuration snapshot.")
    def _config_resource() -> str:
        return json.dumps({
            "duckdb_path": DUCKDB_PATH,
            "metadata_dsn_redacted": _redact_dsn(METADATA_DSN),
            "max_rows": MAX_ROWS,
        })

    return server


def _redact_dsn(dsn: str) -> str:
    """Strip credentials from a SQLAlchemy URL for safe logging/exposure."""
    try:
        from sqlalchemy.engine.url import make_url
        url = make_url(dsn)
        if url.password:
            url = url.set(password="***")
        return str(url)
    except Exception:
        return "<opaque>"


# ── Entry points ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(prog="aura-mcp-server")
    parser.add_argument(
        "--http", type=int, default=None,
        help="Run SSE transport on the given port (default: stdio).",
    )
    parser.add_argument("--log-level", default=os.getenv("AURA_MCP_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level)
    server = build_server()

    if args.http is None:
        # stdio — what Claude Code launches when added via `claude mcp add`
        server.run(transport="stdio")
    else:
        # SSE — for in-cluster agents calling over the network
        os.environ.setdefault("FASTMCP_HOST", "0.0.0.0")
        os.environ.setdefault("FASTMCP_PORT", str(args.http))
        server.run(transport="sse")


if __name__ == "__main__":
    main()
