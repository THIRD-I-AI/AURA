"""
Pipeline Execution Engine
=========================
Executes a Pipeline definition: reads source → applies processing → writes sink.

Uses DuckDB as the in-process SQL engine for transforms.  Reads from / writes to
multiple source/sink types via the existing AURA connector system.

Thread-safe: each execution gets its own DuckDB connection.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from pipeline.models import (
    Pipeline,
    PipelineRun,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)

logger = logging.getLogger("aura.pipeline.engine")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "processed")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# The DuckDB sink's tables live in one database file per tenant (BUG-279).
DUCKDB_SINK_FILE = "pipeline_tables.duckdb"
_duckdb_sink_lock = threading.Lock()

_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _sanitize_id(name: str) -> str:
    """Make a string safe for use as a SQL identifier."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if cleaned and cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned or "col"


def _typed_arrow_table(columns: List[str], rows: List[Dict[str, Any]]) -> Any:
    """Rows from an external source as an Arrow table that keeps each column's type.

    BUG-280: these rows used to be loaded as all-VARCHAR, so a filter, sort or MIN/MAX
    on a numeric or date column compared text ('95' > '100') and returned wrong rows
    with a SUCCESS status. A column whose values Arrow cannot give one type to (mixed
    types, UUIDs, nested JSON) is still loaded as text, as every column was before.
    """
    import json

    import pyarrow as pa

    def _text(v: Any) -> Optional[str]:
        if v is None:
            return None
        return json.dumps(v) if isinstance(v, (dict, list)) else str(v)

    arrays = []
    for c in columns:
        values = [r.get(c) for r in rows]
        try:
            if any(isinstance(v, (dict, list)) for v in values):
                raise TypeError("nested value")
            arr = pa.array(values)
            if pa.types.is_null(arr.type):
                arr = arr.cast(pa.string())
        except (pa.ArrowException, TypeError, ValueError, OverflowError):
            arr = pa.array([_text(v) for v in values], type=pa.string())
        arrays.append(arr)
    return pa.Table.from_arrays(arrays, names=[_sanitize_id(c) for c in columns])


MAX_AUTO_NAMED_OUTPUTS = 50


def _prune_auto_named_outputs(tenant_dir: str) -> None:
    """Keep only the newest auto-named outputs in a tenant's output directory.

    BUG-289: a run with no ``file_name`` writes a fresh ``pipeline_output_<run_id>``
    file and nothing ever deleted one, so a scheduled or repeated pipeline filled the
    volume every tenant shares. Files the caller named are left alone -- a re-run
    overwrites those in place.
    """
    try:
        auto = [
            os.path.join(tenant_dir, name) for name in os.listdir(tenant_dir)
            if name.startswith("pipeline_output_run_")
        ]
        auto.sort(key=os.path.getmtime, reverse=True)
        for path in auto[MAX_AUTO_NAMED_OUTPUTS:]:
            os.remove(path)
    except OSError as exc:
        logger.warning("[Pipeline] Could not prune old outputs in %s: %s", tenant_dir, exc)


# Aliased, not reimplemented. This file had its own _q() that doubled quotes
# but skipped the NUL-byte rejection added to shared/sql_identifiers.py during
# the SQL-injection hardening — so two of the three "quote an identifier"
# implementations in this repo silently missed that guard while the third had
# it. Aliasing covers every existing call site without touching them.
from shared.sql_expression_guard import validate_sql_expression as _validate_expression  # noqa: E402
from shared.sql_identifiers import quote_identifier as _q  # noqa: E402
from shared.sql_identifiers import quote_literal  # noqa: E402

# Aggregates PIVOT ... USING may apply (BUG-184). Anything else is refused, not spliced.
_MAX_SOURCE_ROWS = 100_000

_PIVOT_AGG_FUNCTIONS = frozenset({
    "SUM", "AVG", "COUNT", "MIN", "MAX", "MEDIAN", "FIRST", "LAST", "ANY_VALUE",
    "STDDEV", "STDDEV_SAMP", "STDDEV_POP", "VARIANCE", "VAR_SAMP", "VAR_POP",
})
from shared.storage.base import tenant_slug  # noqa: E402


class PipelineEngine:
    """Executes Pipeline definitions using DuckDB.

    Stateless (S50): pipeline storage moved to the durable, tenant-scoped
    gateway persistence layer (api_gateway/persistence.py). The engine only
    executes a passed definition, so it is safe to instantiate per call and
    correct across replicas.
    """

    # ── Execute ───────────────────────────────────────────────────────

    async def execute(
        self,
        pipeline: Pipeline,
        preview_only: bool = False,
        preview_limit: int = 50,
        source_progress_cb: Optional[Callable[[int, Optional[int]], Awaitable[None]]] = None,
        tenant: Optional[str] = None,
    ) -> PipelineRun:
        """
        Run the full pipeline: Source → Process → Sink.
        Returns a PipelineRun with results/metadata.

        ``tenant``: the caller's tenant id — e.g.
        ``api_gateway.routers.workspaces._request_tenant(request)`` — used to
        scope FILE and external-file DUCKDB sources through the active
        ``shared.storage`` ``StorageBackend`` (BUG-035; mirrors etl.py's
        endpoints). ``None`` resolves to the backend's shared "default"
        tenant bucket, matching an unauthenticated request's upload target.
        Callers with no request context (CLI, tests) may omit it.
        """
        from shared.duckdb_factory import lock_down_connection, new_connection

        t0 = time.perf_counter()
        run = PipelineRun(pipeline_id=pipeline.id)
        # new_connection() (not a bare duckdb.connect) so the active storage
        # backend gets to configure_duckdb() this connection — S3 mode needs
        # httpfs installed + its secret registered before a FILE/DUCKDB
        # source's s3:// URI can be read at all.
        conn = new_connection()

        try:
            # ── 1. LOAD SOURCE ────────────────────────────────────────
            source_table = await self._load_source(conn, pipeline.source, source_progress_cb, tenant)
            logger.info(f"[Pipeline:{pipeline.id}] Source loaded as '{source_table}'")

            # Count source rows
            src_count = (await asyncio.to_thread(
                conn.execute, f"SELECT COUNT(*) FROM {_q(source_table)}"
            )).fetchone()[0]
            run.rows_read = src_count

            # Source columns
            src_cols = [desc[0] for desc in (await asyncio.to_thread(
                conn.execute, f"SELECT * FROM {_q(source_table)} LIMIT 0"
            )).description]
            run.columns_in = src_cols

            # ── 2. LOAD JOIN SOURCES (BUG-084) ────────────────────────
            # A JOIN step's second source must be loaded into the same
            # connection under its own table name before the CTE chain
            # references it -- loading is async/blocking I/O, so it can't
            # happen inside the (sync) SQL-building pass below.
            join_tables: Dict[str, str] = {}
            for step in pipeline.steps:
                if step.type == StepType.JOIN and step.join_source is not None:
                    join_source = PipelineSource(**step.join_source.model_dump())
                    join_table_name = f"join_src_{_sanitize_id(step.id)}"
                    join_tables[step.id] = await self._load_source(
                        conn, join_source, tenant=tenant, table_name=join_table_name,
                    )

            # BUG-276: every table the run needs is now materialised in `conn`, and what
            # runs next is step SQL written by the caller or an LLM. The expression
            # guard in front of it is a blocklist and was bypassable (a double-quoted
            # path, or a string literal after a comma, is a DuckDB replacement scan
            # that reads any local file -- including another tenant's uploads). Cut
            # the connection off from the filesystem and network instead; the sink
            # writes through a separate connection.
            await asyncio.to_thread(lock_down_connection, conn)

            # ── 3. BUILD PROCESSING SQL ───────────────────────────────
            final_table, sql, steps_run, steps_skip = self._build_processing_sql(
                conn, source_table, pipeline.steps, join_tables
            )
            run.sql_generated = sql
            run.steps_executed = steps_run
            run.steps_skipped = steps_skip

            logger.info(f"[Pipeline:{pipeline.id}] SQL:\n{sql}")

            # Execute the transform chain (CPU-bound DuckDB work — the
            # deployment runs one uvicorn worker, so running this inline
            # would freeze every concurrent request for its duration).
            await asyncio.to_thread(conn.execute, sql)

            # Get output metadata
            out_count = (await asyncio.to_thread(
                conn.execute, f"SELECT COUNT(*) FROM {_q(final_table)}"
            )).fetchone()[0]
            out_cols = [desc[0] for desc in (await asyncio.to_thread(
                conn.execute, f"SELECT * FROM {_q(final_table)} LIMIT 0"
            )).description]
            run.rows_written = out_count
            run.columns_out = out_cols

            # Preview rows
            preview_rows = (await asyncio.to_thread(
                conn.execute, f"SELECT * FROM {_q(final_table)} LIMIT {preview_limit}"
            )).fetchall()
            col_descs = [desc[0] for desc in (await asyncio.to_thread(
                conn.execute, f"SELECT * FROM {_q(final_table)} LIMIT 0"
            )).description]
            run.preview_data = [dict(zip(col_descs, row)) for row in preview_rows]

            # ── 3. WRITE SINK (unless preview_only) ───────────────────
            if not preview_only:
                await self._write_sink(conn, final_table, pipeline.sink, run, tenant)
            else:
                logger.info(f"[Pipeline:{pipeline.id}] Preview-only, skipping sink write")

            run.status = PipelineStatus.SUCCESS

        except Exception as exc:
            logger.error(f"[Pipeline:{pipeline.id}] Execution failed: {exc}", exc_info=True)
            run.status = PipelineStatus.FAILED
            run.error = str(exc)
        finally:
            run.duration_ms = (time.perf_counter() - t0) * 1000
            from datetime import datetime, timezone
            run.finished_at = datetime.now(timezone.utc).isoformat()
            conn.close()

        return run

    # ── Source Loading ────────────────────────────────────────────────

    async def _load_source(
        self,
        conn: Any,
        source: PipelineSource,
        progress_cb: Optional[Callable[[int, Optional[int]], Awaitable[None]]] = None,
        tenant: Optional[str] = None,
        table_name: str = "source_data",
    ) -> str:
        """Load source data into DuckDB and return the table name.

        ``table_name``: defaults to "source_data" (the primary source's
        historical fixed name). A JOIN step's second source (BUG-084) must
        load under a distinct name, since both sources share one connection
        and every loader below otherwise hardcodes this same name.
        """
        # _load_file_source (a network LLM call via smart_load_file) and
        # _load_duckdb_source (ATTACH + CREATE TABLE AS on a large external
        # file) both block; the deployment runs one uvicorn worker, so
        # running either inline would freeze every concurrent request for
        # its duration (same reasoning as the conn.execute calls below).
        if source.type == SourceType.FILE:
            return await asyncio.to_thread(self._load_file_source, conn, source, tenant, table_name)
        elif source.type in (SourceType.POSTGRESQL, SourceType.MYSQL):
            return await self._load_db_source(conn, source, table_name)
        elif source.type == SourceType.DUCKDB:
            return await asyncio.to_thread(self._load_duckdb_source, conn, source, tenant, table_name)
        elif source.type == SourceType.KAFKA:
            return await self._load_kafka_source(conn, source, progress_cb, table_name)
        else:
            raise ValueError(f"Unsupported source type: {source.type}")

    def _load_file_source(
        self, conn: Any, source: PipelineSource, tenant: Optional[str] = None, table_name: str = "source_data",
    ) -> str:
        """Load a CSV/Parquet/JSON file into DuckDB with smart header detection.

        BUG-035: reads through the active StorageBackend (mirrors
        api_gateway/routers/etl.py's etl_execute/etl_preview_source) instead
        of a local-filesystem-only path, so this works under both
        AURA_STORAGE_BACKEND=local and =s3. safe_object_name() is the
        path-traversal guard — same containment guarantee _resolve_in_dir
        used to provide, enforced at the layer the backend itself owns.
        """
        from shared.data_utils import smart_load_file
        from shared.storage import get_storage_backend
        from shared.storage.base import safe_object_name

        fname = source.file_name
        if not fname:
            raise ValueError("File source requires file_name")

        backend = get_storage_backend()
        try:
            safe_name = safe_object_name(fname)
        except ValueError:
            raise ValueError(f"Invalid source file name: {fname}")
        if not backend.exists(tenant, safe_name):
            raise FileNotFoundError(f"Source file not found: {fname}")
        duckdb_uri = backend.duckdb_uri(tenant, safe_name)

        smart_load_file(conn, duckdb_uri, table_name, use_llm=True)
        return table_name

    async def _load_db_source(self, conn: Any, source: PipelineSource, table_name: str = "source_data") -> str:
        """Load data from PostgreSQL/MySQL into DuckDB via connector."""
        from connectors import ConnectorConfig, MySQLConnector, PostgreSQLConnector
        from connectors import SourceType as CSourceType

        cfg = source.connection or {}
        src_type = CSourceType.POSTGRESQL if source.type == SourceType.POSTGRESQL else CSourceType.MYSQL
        connector_cls = PostgreSQLConnector if source.type == SourceType.POSTGRESQL else MySQLConnector

        connector = connector_cls(ConnectorConfig(
            source_type=src_type,
            name="pipeline_source",
            host=cfg.get("host", "localhost"),
            port=cfg.get("port"),
            username=cfg.get("username", "postgres"),
            password=cfg.get("password", ""),
            database=cfg.get("database", "postgres"),
        ))

        connected = await connector.connect()
        if not connected:
            raise ConnectionError(f"Cannot connect to {source.type.value} source")

        try:
            query = source.query or f"SELECT * FROM {_q(source.table)}"
            # Ask for one row more than the cap so an over-large source is detected
            # rather than silently truncated; let query errors surface instead of
            # being reported as "no data" (BUG-192).
            rows = await connector.execute_query(
                query, limit=_MAX_SOURCE_ROWS + 1, raise_errors=True
            )
        finally:
            await connector.disconnect()

        if not rows:
            raise ValueError("Source query returned no data")
        if len(rows) > _MAX_SOURCE_ROWS:
            raise ValueError(
                f"Source has more than {_MAX_SOURCE_ROWS:,} rows; narrow the query "
                "(add a WHERE or LIMIT) instead of loading a truncated copy"
            )

        columns = list(rows[0].keys())

        def _create_and_insert() -> None:
            conn.register("external_source_rows", _typed_arrow_table(columns, rows))
            conn.execute(f"CREATE TABLE {_q(table_name)} AS SELECT * FROM external_source_rows")
            conn.unregister("external_source_rows")

        # Same reasoning as _load_source's dispatch: the per-row insert
        # loop blocks the sole uvicorn worker for its whole duration.
        await asyncio.to_thread(_create_and_insert)

        return table_name

    async def _load_kafka_source(
        self,
        conn: Any,
        source: PipelineSource,
        progress_cb: Optional[Callable[[int, Optional[int]], Awaitable[None]]] = None,
        table_name: str = "source_data",
    ) -> str:
        """Consume a bounded batch from a Kafka topic into DuckDB."""
        from shared.kafka_client import consume_batch

        cfg = source.connection or {}
        rows = await consume_batch(cfg, progress_cb=progress_cb)
        if not rows:
            raise ValueError(
                f"Kafka topic '{cfg.get('topic', '?')}' returned no messages "
                f"within timeout_ms={cfg.get('timeout_ms', 5000)}"
            )

        # Union of keys across rows → stable column set
        columns: List[str] = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    columns.append(k)

        def _create_and_insert() -> None:
            conn.register("external_source_rows", _typed_arrow_table(columns, rows))
            conn.execute(f"CREATE TABLE {_q(table_name)} AS SELECT * FROM external_source_rows")
            conn.unregister("external_source_rows")

        # Same reasoning as _load_db_source's dispatch: the per-row insert
        # loop blocks the sole uvicorn worker for its whole duration.
        await asyncio.to_thread(_create_and_insert)

        logger.info("[Pipeline] Kafka source loaded %d rows from %s", len(rows), cfg.get("topic"))
        return table_name

    def _load_duckdb_source(
        self, conn: Any, source: PipelineSource, tenant: Optional[str] = None, table_name: str = "source_data",
    ) -> str:
        """Load from an existing table/query on the engine's own
        in-memory connection, or ATTACH an external .duckdb file named
        in source.connection["database"] and load from that.

        Without the ATTACH, ``source.table``/``source.query`` were
        evaluated against the fresh empty ``:memory:`` connection
        execute() opens, so any real external table raised
        "Catalog Error: Table with name X does not exist!" even though
        the table exists on disk.

        BUG-035: db_path is resolved through the active StorageBackend, same
        as a FILE source. DuckDB's ATTACH has no equivalent of read_csv_auto
        reading an s3:// URI directly for a full external database file —
        that's a materially larger feature (S3 range reads over the whole
        .duckdb file format) than this router-level storage-routing fix, so
        it stays deferred under S3 mode; see the "://" check below.
        """
        from shared.sql_identifiers import quote_literal
        from shared.storage import get_storage_backend
        from shared.storage.base import safe_object_name

        cfg = source.connection or {}
        db_path = cfg.get("database") or cfg.get("path")

        if db_path:
            # SECURITY: db_path is user input (pipeline definition).
            # safe_object_name is the same containment guarantee
            # _resolve_in_dir used to provide, enforced at the layer the
            # backend itself owns — never spliced raw into SQL.
            backend = get_storage_backend()
            try:
                safe_name = safe_object_name(db_path)
            except ValueError:
                raise ValueError(f"Invalid DuckDB source file name: {db_path}")

            # duckdb_uri() is pure string formatting for every backend (no
            # I/O) -- checked BEFORE backend.exists() so the S3 deferral
            # below is reachable with zero network calls, never a redundant
            # existence probe against a file DuckDB's ATTACH can't read
            # anyway. Reordering this would silently reintroduce a network
            # dependency on the deferred path.
            uri = backend.duckdb_uri(tenant, safe_name)
            if "://" in uri:
                raise ValueError(
                    "DUCKDB source type with an external database file is not "
                    "supported under AURA_STORAGE_BACKEND=s3 (deferred — see "
                    "BUG-035); use a FILE source instead, or run this pipeline "
                    "under AURA_STORAGE_BACKEND=local."
                )
            if not backend.exists(tenant, safe_name):
                raise FileNotFoundError(f"DuckDB source file not found: {os.path.basename(str(db_path))}")

            attach_alias = f"ext_{_sanitize_id(table_name)}"
            conn.execute(f"ATTACH {quote_literal(uri)} AS {attach_alias} (READ_ONLY)")
            if source.query:
                conn.execute(f"CREATE TABLE {_q(table_name)} AS {source.query}")
            elif source.table:
                conn.execute(f"CREATE TABLE {_q(table_name)} AS SELECT * FROM {attach_alias}.{_q(source.table)}")
            else:
                raise ValueError("DuckDB source needs table or query")
            return table_name

        if source.query:
            conn.execute(f"CREATE TABLE {_q(table_name)} AS {source.query}")
            return table_name
        if source.table:
            # BUG-093: with no connection.database/path, there is nothing to
            # ATTACH -- `source.table` names a table on some external
            # database that was never materialized on this fresh :memory:
            # connection. Returning it here just defers to a confusing
            # generic "Catalog Error" from execute()'s later SELECT. Fail
            # clearly now, same as the missing-table-or-query case below.
            raise ValueError(
                "DuckDB source with only 'table' set has no database to load "
                "it from -- set connection.database (or connection.path) to "
                "the .duckdb file containing that table, or use 'query' "
                "against data already available on this connection."
            )
        raise ValueError("DuckDB source needs table or query")

    # ── Processing SQL Builder ────────────────────────────────────────

    def _build_processing_sql(
        self,
        conn: Any,
        source_table: str,
        steps: List[ProcessingStep],
        join_tables: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, str, int, int]:
        """
        Build a CTE chain from processing steps.
        Returns (final_table_name, full_sql, steps_executed, steps_skipped).

        ``join_tables``: step.id -> already-loaded DuckDB table name for that
        JOIN step's ``join_source`` (loaded by ``execute()`` before this is
        called, since loading is async/blocking I/O and this method is not).
        """
        join_tables = join_tables or {}
        if not steps:
            # No transforms — create output as passthrough
            sql = f"CREATE TABLE pipeline_output AS SELECT * FROM {_q(source_table)}"
            return "pipeline_output", sql, 0, 0

        ctes: List[str] = []
        prev = source_table
        step_num = 0
        skipped = 0

        for step in steps:
            clause = self._step_to_sql(conn, step, prev, join_tables)
            if clause is None:
                skipped += 1
                continue
            step_num += 1
            alias = f"step_{step_num}"
            ctes.append(f"{alias} AS (\n  {clause}\n)")
            prev = alias

        if not ctes:
            sql = f"CREATE TABLE pipeline_output AS SELECT * FROM {_q(source_table)}"
            return "pipeline_output", sql, 0, skipped

        cte_sql = "WITH " + ",\n".join(ctes)
        sql = f"{cte_sql}\nCREATE TABLE pipeline_output AS SELECT * FROM {_q(prev)}"
        # DuckDB needs CREATE TABLE ... AS WITH ... format:
        sql = f"CREATE TABLE pipeline_output AS {cte_sql} SELECT * FROM {_q(prev)}"
        return "pipeline_output", sql, step_num, skipped

    def _step_to_sql(
        self, conn: Any, step: ProcessingStep, prev: str, join_tables: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Convert a processing step to a SQL SELECT clause.

        Returns None only when the step has genuinely nothing to do (a fill-all with no
        NULLs to fill). A step that is missing a required setting, or names an operator,
        type or function outside its allowlist, raises: it used to be skipped -- or, for
        a filter operator, rewritten to '=' -- and the run still reported SUCCESS on
        rows the step never touched (BUG-281).
        """
        cfg = step.config
        t = step.type

        def _bad(reason: str) -> ValueError:
            return ValueError(f"Pipeline step {t.value!r} is misconfigured: {reason}")

        if t == StepType.FILTER:
            col = cfg.get("column", "")
            op = cfg.get("operator", "=")
            val = cfg.get("value", "")
            if not col:
                raise _bad("no column")
            allowed_ops = {"=", "!=", "<>", ">", "<", ">=", "<=", "LIKE", "NOT LIKE", "IN", "NOT IN", "IS NULL", "IS NOT NULL"}
            if str(op).upper() not in allowed_ops:
                raise _bad(f"unsupported operator {op!r}")
            if op.upper() in ("IS NULL", "IS NOT NULL"):
                return f'SELECT * FROM {_q(prev)} WHERE "{_sanitize_id(col)}" {op}'
            safe_val = str(val).replace("'", "''")
            return f"SELECT * FROM {_q(prev)} WHERE \"{_sanitize_id(col)}\" {op} '{safe_val}'"

        elif t == StepType.SORT:
            col = cfg.get("column", "")
            direction = cfg.get("direction", "ASC").upper()
            if not col:
                raise _bad("no column")
            if direction not in ("ASC", "DESC"):
                raise _bad(f"unsupported direction {direction!r}")
            return f'SELECT * FROM {_q(prev)} ORDER BY "{_sanitize_id(col)}" {direction}'

        elif t == StepType.DROP_COLUMNS:
            columns = cfg.get("columns", [])
            excludes = ", ".join(f'"{_sanitize_id(c)}"' for c in columns if c)
            if not excludes:
                raise _bad("no columns")
            return f"SELECT * EXCLUDE ({excludes}) FROM {_q(prev)}"

        elif t == StepType.RENAME_COLUMNS:
            mapping = cfg.get("mapping", {})
            renames = ", ".join(
                f'"{_sanitize_id(old)}" AS "{_sanitize_id(new)}"'
                for old, new in mapping.items() if old and new
            )
            if not renames:
                raise _bad("no column mapping")
            return f"SELECT {renames}, * EXCLUDE ({', '.join(chr(34) + _sanitize_id(old) + chr(34) for old in mapping if old)}) FROM {_q(prev)}"

        elif t == StepType.ADD_COLUMN:
            name = cfg.get("name", "")
            expression = cfg.get("expression", "")
            if not name or not expression:
                raise _bad("name and expression are both required")
            # BUG-082/083: only the output column name was sanitized; the
            # expression itself (free text, reachable via the LLM generator
            # AND the local rule-based parser's own regex) was spliced
            # verbatim, letting it use read_csv_auto/ATTACH/httpfs against
            # the shared, unrestricted DuckDB connection.
            _validate_expression(expression)
            return f'SELECT *, ({expression}) AS "{_sanitize_id(name)}" FROM {_q(prev)}'

        elif t == StepType.CAST_TYPE:
            col = cfg.get("column", "")
            new_type = cfg.get("new_type", "")
            if not col or not new_type:
                raise _bad("column and new_type are both required")
            allowed_types = {"INTEGER", "VARCHAR", "DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP", "BIGINT", "FLOAT", "TEXT"}
            if str(new_type).upper() not in allowed_types:
                raise _bad(f"unsupported type {new_type!r} (allowed: {', '.join(sorted(allowed_types))})")
            return f'SELECT *, CAST("{_sanitize_id(col)}" AS {new_type.upper()}) AS "{_sanitize_id(col)}_cast" FROM {_q(prev)}'

        elif t == StepType.FILL_MISSING:
            col = cfg.get("column", "")
            value = cfg.get("fill_value", "")
            strategy = cfg.get("strategy", "value")
            if not col:
                raise _bad("no column")

            # ── Fill ALL columns when column is "*" ──
            if col == "*":
                try:
                    schema = conn.execute(f'DESCRIBE {_q(prev)}').fetchall()
                except Exception:
                    return None

                # Detect whether the user's fill value looks numeric
                _val_is_numeric = False
                if value:
                    try:
                        float(str(value))
                        _val_is_numeric = True
                    except ValueError:
                        pass

                # ── Optimisation: only touch columns that actually have NULLs ──
                try:
                    null_count_exprs = ", ".join(
                        f'SUM(CASE WHEN {_q(c)} IS NULL THEN 1 ELSE 0 END) AS {_q(c)}'
                        for c, *_ in schema
                    )
                    null_row = conn.execute(
                        f'SELECT {null_count_exprs} FROM {_q(prev)}'
                    ).fetchone()
                    cols_with_nulls = {schema[j][0] for j, cnt in enumerate(null_row) if cnt and cnt > 0}
                except Exception:
                    cols_with_nulls = None  # fallback: fill all

                replaces = []
                for c_name, c_type, *_ in schema:
                    # Skip columns that have no NULLs (when we could detect)
                    if cols_with_nulls is not None and c_name not in cols_with_nulls:
                        continue
                    is_numeric = any(t in c_type.upper() for t in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "BIGINT", "SMALLINT", "TINYINT", "REAL"))
                    if strategy == "mean" and is_numeric:
                        replaces.append(f'COALESCE({_q(c_name)}, AVG({_q(c_name)}) OVER ()) AS {_q(c_name)}')
                    elif strategy == "median" and is_numeric:
                        replaces.append(f'COALESCE({_q(c_name)}, MEDIAN({_q(c_name)}) OVER ()) AS {_q(c_name)}')
                    elif strategy in ("mean", "median") and not is_numeric:
                        if value and not _val_is_numeric:
                            safe = str(value).replace("'", "''")
                            replaces.append(f"COALESCE({_q(c_name)}, '{safe}') AS {_q(c_name)}")
                        # else: skip text cols for mean/median
                    elif is_numeric and value and _val_is_numeric:
                        # BUG-185: only a value that parsed as a number, re-rendered from the
                        # parsed float -- never the caller's raw string.
                        replaces.append(f'COALESCE({_q(c_name)}, {float(value)!r}) AS {_q(c_name)}')
                    elif is_numeric and not value:
                        replaces.append(f'COALESCE({_q(c_name)}, 0) AS {_q(c_name)}')
                    elif not is_numeric and value and not _val_is_numeric:
                        safe = str(value).replace("'", "''")
                        replaces.append(f"COALESCE({_q(c_name)}, '{safe}') AS {_q(c_name)}")
                    # else: text column + numeric value → skip
                if not replaces:
                    return None
                return f'SELECT * REPLACE ({", ".join(replaces)}) FROM {_q(prev)}'

            if strategy == "mean":
                return f'SELECT *, COALESCE("{_sanitize_id(col)}", AVG("{_sanitize_id(col)}") OVER ()) AS "{_sanitize_id(col)}_filled" FROM {_q(prev)}'
            elif strategy == "median":
                return f'SELECT *, COALESCE("{_sanitize_id(col)}", MEDIAN("{_sanitize_id(col)}") OVER ()) AS "{_sanitize_id(col)}_filled" FROM {_q(prev)}'
            else:
                safe_val = str(value).replace("'", "''")
                return f"SELECT *, COALESCE(\"{_sanitize_id(col)}\", '{safe_val}') AS \"{_sanitize_id(col)}_filled\" FROM {_q(prev)}"

        elif t == StepType.DEDUPLICATE:
            columns = cfg.get("columns", [])
            if columns:
                cols = ", ".join(f'"{_sanitize_id(c)}"' for c in columns if c)
                return f"SELECT DISTINCT ON ({cols}) * FROM {_q(prev)}"
            return f"SELECT DISTINCT * FROM {_q(prev)}"

        elif t == StepType.AGGREGATE:
            group_by = cfg.get("group_by", [])
            aggregations = cfg.get("aggregations", [])
            if not group_by or not aggregations:
                raise _bad("group_by and aggregations are both required")
            gb_cols = ", ".join(f'"{_sanitize_id(c)}"' for c in group_by if c)
            agg_parts = []
            for agg in aggregations:
                func = agg.get("function", "COUNT").upper()
                col = agg.get("column", "*")
                alias = agg.get("alias", f"{func}_{col}")
                allowed_funcs = {"COUNT", "SUM", "AVG", "MIN", "MAX", "MEDIAN", "STDDEV"}
                if func not in allowed_funcs:
                    raise _bad(f"unsupported aggregate function {func!r}")
                col_ref = f'"{_sanitize_id(col)}"' if col != "*" else "*"
                agg_parts.append(f'{func}({col_ref}) AS "{_sanitize_id(alias)}"')
            return f"SELECT {gb_cols}, {', '.join(agg_parts)} FROM {_q(prev)} GROUP BY {gb_cols}"

        elif t == StepType.JOIN:
            join_type = cfg.get("join_type", "INNER").upper()
            left_key = cfg.get("left_key", "")
            right_key = cfg.get("right_key", "")
            # BUG-084: `right_table` used to be a bare name from cfg, with
            # no code path anywhere loading that second source into the
            # connection -- every JOIN failed (Catalog Error) or, worse,
            # accidentally matched an internal CTE alias like "step_1".
            # `step.join_source` (models.py) is the real second-source
            # descriptor; execute() loads it before this runs and passes
            # the resulting table name here keyed by step.id.
            right_table = (join_tables or {}).get(step.id)
            if not right_table:
                raise _bad("no join_source to join against")
            if not left_key or not right_key:
                raise _bad("left_key and right_key are both required")
            allowed_joins = {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}
            if join_type not in allowed_joins:
                raise _bad(f"unsupported join type {join_type!r}")
            return (
                f"SELECT * FROM {_q(prev)} "
                f'{join_type} JOIN {_q(right_table)} '
                f'ON {_q(prev)}."{_sanitize_id(left_key)}" = {_q(right_table)}."{_sanitize_id(right_key)}"'
            )

        elif t == StepType.WINDOW:
            function = cfg.get("function", "ROW_NUMBER").upper()
            partition_by = cfg.get("partition_by", [])
            order_by = cfg.get("order_by", "")
            alias = cfg.get("alias", "window_result")
            if not order_by:
                raise _bad("no order_by")
            allowed_win = {"ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "SUM", "AVG", "COUNT", "MIN", "MAX", "NTILE"}
            if function not in allowed_win:
                raise _bad(f"unsupported window function {function!r}")
            partition_clause = ""
            if partition_by:
                pb = ", ".join(f'"{_sanitize_id(c)}"' for c in partition_by if c)
                partition_clause = f"PARTITION BY {pb} " if pb else ""
            return (
                f'SELECT *, {function}() OVER ({partition_clause}ORDER BY "{_sanitize_id(order_by)}") '
                f'AS "{_sanitize_id(alias)}" FROM {_q(prev)}'
            )

        elif t == StepType.PIVOT:
            values_col = cfg.get("values_column", "")
            pivot_col = cfg.get("pivot_column", "")
            agg_func = str(cfg.get("agg_function", "SUM")).strip().upper()
            # BUG-184: this used to be spliced raw into `USING <agg>(...)`.
            if agg_func not in _PIVOT_AGG_FUNCTIONS:
                raise _bad(f"unsupported aggregate function {agg_func!r}")
            if not values_col or not pivot_col:
                raise _bad("values_column and pivot_column are both required")
            return (
                f'PIVOT {_q(prev)} ON "{_sanitize_id(pivot_col)}" '
                f'USING {agg_func}("{_sanitize_id(values_col)}")'
            )

        elif t == StepType.UNPIVOT:
            columns = cfg.get("columns", [])
            cols = ", ".join(f'"{_sanitize_id(c)}"' for c in columns if c)
            if not cols:
                raise _bad("no columns")
            return f"UNPIVOT {_q(prev)} ON {cols} INTO NAME variable VALUE value"

        elif t == StepType.LIMIT:
            n = cfg.get("count", 100)
            return f"SELECT * FROM {_q(prev)} LIMIT {int(n)}"

        elif t == StepType.CUSTOM_SQL:
            expression = cfg.get("expression", "").strip()
            if not expression:
                raise _bad("no expression")
            # BUG-082: mirrors etl.py's custom_sql guard (BUG-053) -- this
            # step's expression was never validated at all.
            _validate_expression(expression)
            # Custom SQL must reference {{prev}} as the upstream table
            return expression.replace("{{prev}}", _q(prev))

        # Every StepType above has a branch; reaching here means a step type
        # (e.g. UNION) has no SQL generation implemented yet. Silently
        # returning None here would make the caller skip the step and keep
        # going, producing a wrong result with no error (BUG-010 item 1) —
        # fail the run instead so the caller sees why.
        raise ValueError(f"Pipeline step type {t.value!r} is not implemented")

    # ── Sink Writing ──────────────────────────────────────────────────

    async def _write_sink(
        self, conn: Any, final_table: str, sink: PipelineSink, run: PipelineRun,
        tenant: Optional[str] = None,
    ) -> None:
        """Write the final table to the configured sink."""
        if sink.type == SinkType.FILE:
            # DuckDB COPY over the full final_table is blocking; the
            # deployment runs one uvicorn worker, so offload it (matches
            # the transform SQL execution above).
            await asyncio.to_thread(self._write_file_sink, conn, final_table, sink, run, tenant)
        elif sink.type == SinkType.POSTGRESQL:
            await self._write_pg_sink(conn, final_table, sink, run)
        elif sink.type == SinkType.DUCKDB:
            await asyncio.to_thread(self._write_duckdb_sink, conn, final_table, sink, run, tenant)
        elif sink.type == SinkType.PREVIEW:
            pass  # preview_data already set
        else:
            raise ValueError(f"Unsupported sink type: {sink.type}")

    def _write_file_sink(
        self, conn: Any, final_table: str, sink: PipelineSink, run: PipelineRun,
        tenant: Optional[str] = None,
    ) -> None:
        fmt = (sink.format or "csv").lower()
        base_name = sink.file_name or f"pipeline_output_{run.run_id}"
        # Ensure correct extension
        stem = Path(base_name).stem
        ext_map = {"csv": ".csv", "parquet": ".parquet", "json": ".json"}
        ext = ext_map.get(fmt, ".csv")
        out_name = f"{stem}{ext}"
        # BUG-051: one shared OUTPUT_DIR let any caller download any other
        # tenant's output by guessing/colliding on file_name. Namespace by
        # tenant (same tenant_slug() used for uploaded-source isolation) so
        # GET /pipeline/download/{filename} can only ever resolve inside
        # the requesting caller's own subdirectory.
        tenant_dir = os.path.join(OUTPUT_DIR, tenant_slug(tenant))
        os.makedirs(tenant_dir, exist_ok=True)
        out_path = os.path.join(tenant_dir, out_name)

        # The run's connection is locked down (BUG-276) and cannot COPY to disk, so
        # the result is handed to a fresh connection that runs nothing but this COPY.
        from shared.duckdb_factory import new_connection

        result = conn.execute(f"SELECT * FROM {_q(final_table)}").arrow()
        out = new_connection()
        try:
            out.register("pipeline_output_export", result)
            if fmt == "parquet":
                options = "(FORMAT PARQUET)"
            elif fmt == "json":
                options = "(FORMAT JSON, ARRAY true)"
            else:
                options = "(HEADER, DELIMITER ',')"
            out.execute(f"COPY pipeline_output_export TO {quote_literal(out_path)} {options}")
        finally:
            out.close()

        run.output_file = out_name
        logger.info(f"[Pipeline] Wrote {out_name} ({run.rows_written} rows)")
        if not sink.file_name:
            _prune_auto_named_outputs(tenant_dir)

    async def _write_pg_sink(
        self, conn: Any, final_table: str, sink: PipelineSink, run: PipelineRun
    ) -> None:
        """Write DuckDB table to PostgreSQL."""
        from connectors import ConnectorConfig, PostgreSQLConnector
        from connectors import SourceType as CSourceType

        cfg = sink.connection or {}
        table_name = sink.table or "pipeline_output"
        if sink.if_exists not in ("replace", "append", "fail"):
            raise ValueError(f"Unsupported if_exists for a PostgreSQL sink: {sink.if_exists!r}")

        pg = PostgreSQLConnector(ConnectorConfig(
            source_type=CSourceType.POSTGRESQL,
            name="pipeline_sink",
            host=cfg.get("host", "localhost"),
            port=cfg.get("port", 5432),
            username=cfg.get("username", "postgres"),
            password=cfg.get("password", ""),
            database=cfg.get("database", "postgres"),
        ))

        connected = await pg.connect()
        if not connected:
            raise ConnectionError("Cannot connect to PostgreSQL sink")

        try:
            # Get data from DuckDB (blocking; offload so the event loop stays free)
            result = await asyncio.to_thread(conn.execute, f"SELECT * FROM {_q(final_table)}")
            col_names = [desc[0] for desc in result.description]
            rows = await asyncio.to_thread(result.fetchall)

            if not pg.pool:
                raise ConnectionError("PostgreSQL pool not available")

            # Read the DuckDB schema before touching the destination.
            duck_schema = await asyncio.to_thread(
                lambda: conn.execute(f"DESCRIBE {_q(final_table)}").fetchall()
            )

            async with pg.pool.acquire() as pg_conn, pg_conn.transaction():
                # BUG-191: DROP, CREATE and INSERT are one transaction (Postgres DDL is
                # transactional), so a failure part-way rolls back to the previous table
                # instead of leaving it missing or half-loaded.
                if sink.if_exists == "replace":
                    await pg_conn.execute(f"DROP TABLE IF EXISTS {_q(table_name)}")
                pg_type_map = {
                    "INTEGER": "INTEGER", "BIGINT": "BIGINT", "DOUBLE": "DOUBLE PRECISION",
                    "FLOAT": "REAL", "VARCHAR": "TEXT", "BOOLEAN": "BOOLEAN",
                    "DATE": "DATE", "TIMESTAMP": "TIMESTAMP",
                }
                col_defs = []
                for row in duck_schema:
                    duck_type = row[1].upper()
                    pg_type = pg_type_map.get(duck_type, "TEXT")
                    col_defs.append(f'{_q(row[0])} {pg_type}')

                # BUG-285: 'fail' used to share replace's CREATE TABLE IF NOT EXISTS and
                # then insert, so it appended into the table it was meant to protect.
                # A plain CREATE TABLE raises on an existing table before any row is
                # written. 'append' creates the table only when it is missing.
                if_not_exists = "IF NOT EXISTS " if sink.if_exists == "append" else ""
                try:
                    await pg_conn.execute(
                        f"CREATE TABLE {if_not_exists}{_q(table_name)} ({', '.join(col_defs)})"
                    )
                except Exception as exc:
                    if sink.if_exists == "fail" and getattr(exc, "sqlstate", None) == "42P07":
                        raise ValueError(
                            f"Table {table_name!r} already exists and if_exists is 'fail'"
                        ) from exc
                    raise

                # Insert rows in batches
                if rows:
                    placeholders = ", ".join(f"${i+1}" for i in range(len(col_names)))
                    insert_sql = f"INSERT INTO {_q(table_name)} VALUES ({placeholders})"
                    await pg_conn.executemany(insert_sql, rows)

            run.output_table = table_name
            logger.info(f"[Pipeline] Wrote {len(rows)} rows to PostgreSQL: {table_name}")

        finally:
            await pg.disconnect()

    def _write_duckdb_sink(
        self, conn: Any, final_table: str, sink: PipelineSink, run: PipelineRun,
        tenant: Optional[str] = None,
    ) -> None:
        # BUG-279: this used to CREATE TABLE on `conn` -- the run's own in-memory
        # connection, closed as soon as execute() returns -- so the run reported
        # success with output_table set and nothing was kept. The table now goes
        # into the tenant's own database file, next to its file-sink outputs, where
        # GET /pipeline/download/{output_file} serves it.
        from shared.duckdb_factory import new_connection

        table_name = sink.table or "pipeline_output_saved"
        if sink.if_exists not in ("replace", "append", "fail"):
            raise ValueError(f"Unsupported if_exists for a DuckDB sink: {sink.if_exists!r}")
        tenant_dir = os.path.join(OUTPUT_DIR, tenant_slug(tenant))
        os.makedirs(tenant_dir, exist_ok=True)
        db_path = os.path.join(tenant_dir, DUCKDB_SINK_FILE)

        result = conn.execute(f"SELECT * FROM {_q(final_table)}").arrow()
        with _duckdb_sink_lock:
            out = new_connection(db_path)
            try:
                out.register("pipeline_output_export", result)
                exists = out.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'main' AND table_name = ?", [table_name],
                ).fetchone() is not None
                if exists and sink.if_exists == "fail":
                    raise ValueError(f"Table {table_name!r} already exists and if_exists is 'fail'")
                if exists and sink.if_exists == "append":
                    out.execute(f"INSERT INTO {_q(table_name)} BY NAME SELECT * FROM pipeline_output_export")
                else:
                    out.execute(
                        f"CREATE OR REPLACE TABLE {_q(table_name)} AS SELECT * FROM pipeline_output_export")
            finally:
                out.close()

        run.output_table = table_name
        run.output_file = DUCKDB_SINK_FILE
        logger.info(f"[Pipeline] Wrote to DuckDB table: {table_name}")
