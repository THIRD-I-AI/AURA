"""
Pipeline Execution Engine
=========================
Executes a Pipeline definition: reads source → applies processing → writes sink.

Uses DuckDB as the in-process SQL engine for transforms.  Reads from / writes to
multiple source/sink types via the existing AURA connector system.

Thread-safe: each execution gets its own DuckDB connection.
"""
from __future__ import annotations

import os
import re
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pipeline.models import (
    Pipeline,
    PipelineRun,
    PipelineSource,
    PipelineSink,
    ProcessingStep,
    PipelineStatus,
    SourceType,
    SinkType,
    StepType,
)

logger = logging.getLogger("aura.pipeline.engine")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "processed")
os.makedirs(OUTPUT_DIR, exist_ok=True)

_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _sanitize_id(name: str) -> str:
    """Make a string safe for use as a SQL identifier."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if cleaned and cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned or "col"


def _q(name: str) -> str:
    """Double-quote a SQL identifier to handle special characters like & in table names."""
    return '"' + name.replace('"', '""') + '"'


class PipelineEngine:
    """Executes Pipeline definitions using DuckDB."""

    def __init__(self) -> None:
        self._pipelines: Dict[str, Pipeline] = {}  # in-memory store

    # ── Pipeline CRUD ─────────────────────────────────────────────────

    def save(self, pipeline: Pipeline) -> Pipeline:
        from datetime import datetime
        pipeline.updated_at = datetime.utcnow().isoformat()
        self._pipelines[pipeline.id] = pipeline
        return pipeline

    def get(self, pipeline_id: str) -> Optional[Pipeline]:
        return self._pipelines.get(pipeline_id)

    def list_all(self) -> List[Pipeline]:
        return list(self._pipelines.values())

    def delete(self, pipeline_id: str) -> bool:
        return self._pipelines.pop(pipeline_id, None) is not None

    # ── Execute ───────────────────────────────────────────────────────

    async def execute(
        self,
        pipeline: Pipeline,
        preview_only: bool = False,
        preview_limit: int = 50,
    ) -> PipelineRun:
        """
        Run the full pipeline: Source → Process → Sink.
        Returns a PipelineRun with results/metadata.
        """
        import duckdb

        t0 = time.perf_counter()
        run = PipelineRun(pipeline_id=pipeline.id)
        conn = duckdb.connect(":memory:")

        try:
            # ── 1. LOAD SOURCE ────────────────────────────────────────
            source_table = await self._load_source(conn, pipeline.source)
            logger.info(f"[Pipeline:{pipeline.id}] Source loaded as '{source_table}'")

            # Count source rows
            src_count = conn.execute(f"SELECT COUNT(*) FROM {_q(source_table)}").fetchone()[0]
            run.rows_read = src_count

            # Source columns
            src_cols = [desc[0] for desc in conn.execute(f"SELECT * FROM {_q(source_table)} LIMIT 0").description]
            run.columns_in = src_cols

            # ── 2. BUILD PROCESSING SQL ───────────────────────────────
            final_table, sql, steps_run, steps_skip = self._build_processing_sql(
                conn, source_table, pipeline.steps
            )
            run.sql_generated = sql
            run.steps_executed = steps_run
            run.steps_skipped = steps_skip

            logger.info(f"[Pipeline:{pipeline.id}] SQL:\n{sql}")

            # Execute the transform chain
            conn.execute(sql)

            # Get output metadata
            out_count = conn.execute(f"SELECT COUNT(*) FROM {_q(final_table)}").fetchone()[0]
            out_cols = [desc[0] for desc in conn.execute(f"SELECT * FROM {_q(final_table)} LIMIT 0").description]
            run.rows_written = out_count
            run.columns_out = out_cols

            # Preview rows
            preview_rows = conn.execute(
                f"SELECT * FROM {_q(final_table)} LIMIT {preview_limit}"
            ).fetchall()
            col_descs = [desc[0] for desc in conn.execute(f"SELECT * FROM {_q(final_table)} LIMIT 0").description]
            run.preview_data = [dict(zip(col_descs, row)) for row in preview_rows]

            # ── 3. WRITE SINK (unless preview_only) ───────────────────
            if not preview_only:
                await self._write_sink(conn, final_table, pipeline.sink, run)
            else:
                logger.info(f"[Pipeline:{pipeline.id}] Preview-only, skipping sink write")

            run.status = PipelineStatus.SUCCESS

        except Exception as exc:
            logger.error(f"[Pipeline:{pipeline.id}] Execution failed: {exc}", exc_info=True)
            run.status = PipelineStatus.FAILED
            run.error = str(exc)
        finally:
            run.duration_ms = (time.perf_counter() - t0) * 1000
            from datetime import datetime
            run.finished_at = datetime.utcnow().isoformat()
            conn.close()

        return run

    # ── Source Loading ────────────────────────────────────────────────

    async def _load_source(self, conn: Any, source: PipelineSource) -> str:
        """Load source data into DuckDB and return the table name."""
        if source.type == SourceType.FILE:
            return self._load_file_source(conn, source)
        elif source.type in (SourceType.POSTGRESQL, SourceType.MYSQL):
            return await self._load_db_source(conn, source)
        elif source.type == SourceType.DUCKDB:
            return self._load_duckdb_source(conn, source)
        else:
            raise ValueError(f"Unsupported source type: {source.type}")

    def _load_file_source(self, conn: Any, source: PipelineSource) -> str:
        """Load a CSV/Parquet/JSON file into DuckDB with smart header detection."""
        from shared.data_utils import smart_load_file

        fname = source.file_name
        if not fname:
            raise ValueError("File source requires file_name")

        file_path = os.path.join(UPLOAD_DIR, fname)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Source file not found: {fname}")

        table_name = "source_data"
        smart_load_file(conn, file_path, table_name, use_llm=True)
        return table_name

    async def _load_db_source(self, conn: Any, source: PipelineSource) -> str:
        """Load data from PostgreSQL/MySQL into DuckDB via connector."""
        from connectors import ConnectorConfig, SourceType as CSourceType, PostgreSQLConnector, MySQLConnector

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
            rows = await connector.execute_query(query, limit=100_000)
        finally:
            await connector.disconnect()

        if not rows:
            raise ValueError("Source query returned no data")

        # Load into DuckDB
        import duckdb
        table_name = "source_data"
        columns = list(rows[0].keys())
        col_defs = ", ".join(f'"{_sanitize_id(c)}" VARCHAR' for c in columns)
        conn.execute(f"CREATE TABLE {_q(table_name)} ({col_defs})")

        for row in rows:
            values = ", ".join(f"'{str(v).replace(chr(39), chr(39)+chr(39))}'" if v is not None else "NULL" for v in row.values())
            conn.execute(f"INSERT INTO {_q(table_name)} VALUES ({values})")

        return table_name

    def _load_duckdb_source(self, conn: Any, source: PipelineSource) -> str:
        """Load from existing DuckDB table or query."""
        if source.query:
            conn.execute(f"CREATE TABLE source_data AS {source.query}")
            return "source_data"
        if source.table:
            return source.table
        raise ValueError("DuckDB source needs table or query")

    # ── Processing SQL Builder ────────────────────────────────────────

    def _build_processing_sql(
        self,
        conn: Any,
        source_table: str,
        steps: List[ProcessingStep],
    ) -> Tuple[str, str, int, int]:
        """
        Build a CTE chain from processing steps.
        Returns (final_table_name, full_sql, steps_executed, steps_skipped).
        """
        if not steps:
            # No transforms — create output as passthrough
            sql = f"CREATE TABLE pipeline_output AS SELECT * FROM {_q(source_table)}"
            return "pipeline_output", sql, 0, 0

        ctes: List[str] = []
        prev = source_table
        step_num = 0
        skipped = 0

        for step in steps:
            clause = self._step_to_sql(conn, step, prev)
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

    def _step_to_sql(self, conn: Any, step: ProcessingStep, prev: str) -> Optional[str]:
        """Convert a processing step to a SQL SELECT clause. Returns None to skip."""
        cfg = step.config
        t = step.type

        if t == StepType.FILTER:
            col = cfg.get("column", "")
            op = cfg.get("operator", "=")
            val = cfg.get("value", "")
            if not col:
                return None
            allowed_ops = {"=", "!=", ">", "<", ">=", "<=", "LIKE", "NOT LIKE", "IN", "NOT IN", "IS NULL", "IS NOT NULL"}
            if op.upper() not in allowed_ops:
                op = "="
            if op.upper() in ("IS NULL", "IS NOT NULL"):
                return f'SELECT * FROM {_q(prev)} WHERE "{_sanitize_id(col)}" {op}'
            safe_val = str(val).replace("'", "''")
            return f"SELECT * FROM {_q(prev)} WHERE \"{_sanitize_id(col)}\" {op} '{safe_val}'"

        elif t == StepType.SORT:
            col = cfg.get("column", "")
            direction = cfg.get("direction", "ASC").upper()
            if not col:
                return None
            if direction not in ("ASC", "DESC"):
                direction = "ASC"
            return f'SELECT * FROM {_q(prev)} ORDER BY "{_sanitize_id(col)}" {direction}'

        elif t == StepType.DROP_COLUMNS:
            columns = cfg.get("columns", [])
            if not columns:
                return None
            excludes = ", ".join(f'"{_sanitize_id(c)}"' for c in columns if c)
            if not excludes:
                return None
            return f"SELECT * EXCLUDE ({excludes}) FROM {_q(prev)}"

        elif t == StepType.RENAME_COLUMNS:
            mapping = cfg.get("mapping", {})
            if not mapping:
                return None
            renames = ", ".join(
                f'"{_sanitize_id(old)}" AS "{_sanitize_id(new)}"'
                for old, new in mapping.items() if old and new
            )
            if not renames:
                return None
            return f"SELECT {renames}, * EXCLUDE ({', '.join(chr(34) + _sanitize_id(old) + chr(34) for old in mapping if old)}) FROM {_q(prev)}"

        elif t == StepType.ADD_COLUMN:
            name = cfg.get("name", "")
            expression = cfg.get("expression", "")
            if not name or not expression:
                return None
            return f'SELECT *, ({expression}) AS "{_sanitize_id(name)}" FROM {_q(prev)}'

        elif t == StepType.CAST_TYPE:
            col = cfg.get("column", "")
            new_type = cfg.get("new_type", "")
            if not col or not new_type:
                return None
            allowed_types = {"INTEGER", "VARCHAR", "DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP", "BIGINT", "FLOAT", "TEXT"}
            if new_type.upper() not in allowed_types:
                return None
            return f'SELECT *, CAST("{_sanitize_id(col)}" AS {new_type.upper()}) AS "{_sanitize_id(col)}_cast" FROM {_q(prev)}'

        elif t == StepType.FILL_MISSING:
            col = cfg.get("column", "")
            value = cfg.get("fill_value", "")
            strategy = cfg.get("strategy", "value")
            if not col:
                return None

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
                        f'SUM(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END) AS "{c}"'
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
                        replaces.append(f'COALESCE("{c_name}", AVG("{c_name}") OVER ()) AS "{c_name}"')
                    elif strategy == "median" and is_numeric:
                        replaces.append(f'COALESCE("{c_name}", MEDIAN("{c_name}") OVER ()) AS "{c_name}"')
                    elif strategy in ("mean", "median") and not is_numeric:
                        if value and not _val_is_numeric:
                            safe = str(value).replace("'", "''")
                            replaces.append(f"COALESCE(\"{c_name}\", '{safe}') AS \"{c_name}\"")
                        # else: skip text cols for mean/median
                    elif is_numeric and value:
                        replaces.append(f'COALESCE("{c_name}", {value}) AS "{c_name}"')
                    elif is_numeric and not value:
                        replaces.append(f'COALESCE("{c_name}", 0) AS "{c_name}"')
                    elif not is_numeric and value and not _val_is_numeric:
                        safe = str(value).replace("'", "''")
                        replaces.append(f"COALESCE(\"{c_name}\", '{safe}') AS \"{c_name}\"")
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
                return None
            gb_cols = ", ".join(f'"{_sanitize_id(c)}"' for c in group_by if c)
            agg_parts = []
            for agg in aggregations:
                func = agg.get("function", "COUNT").upper()
                col = agg.get("column", "*")
                alias = agg.get("alias", f"{func}_{col}")
                allowed_funcs = {"COUNT", "SUM", "AVG", "MIN", "MAX", "MEDIAN", "STDDEV"}
                if func not in allowed_funcs:
                    continue
                col_ref = f'"{_sanitize_id(col)}"' if col != "*" else "*"
                agg_parts.append(f'{func}({col_ref}) AS "{_sanitize_id(alias)}"')
            if not agg_parts:
                return None
            return f"SELECT {gb_cols}, {', '.join(agg_parts)} FROM {_q(prev)} GROUP BY {gb_cols}"

        elif t == StepType.JOIN:
            join_type = cfg.get("join_type", "INNER").upper()
            left_key = cfg.get("left_key", "")
            right_key = cfg.get("right_key", "")
            right_table = cfg.get("right_table", "")
            if not left_key or not right_key or not right_table:
                return None
            allowed_joins = {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}
            if join_type not in allowed_joins:
                join_type = "INNER"
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
                return None
            allowed_win = {"ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "SUM", "AVG", "COUNT", "MIN", "MAX", "NTILE"}
            if function not in allowed_win:
                return None
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
            agg_func = cfg.get("agg_function", "SUM").upper()
            if not values_col or not pivot_col:
                return None
            return (
                f'PIVOT {_q(prev)} ON "{_sanitize_id(pivot_col)}" '
                f'USING {agg_func}("{_sanitize_id(values_col)}")'
            )

        elif t == StepType.UNPIVOT:
            columns = cfg.get("columns", [])
            if not columns:
                return None
            cols = ", ".join(f'"{_sanitize_id(c)}"' for c in columns if c)
            return f"UNPIVOT {_q(prev)} ON {cols} INTO NAME variable VALUE value"

        elif t == StepType.LIMIT:
            n = cfg.get("count", 100)
            return f"SELECT * FROM {_q(prev)} LIMIT {int(n)}"

        elif t == StepType.CUSTOM_SQL:
            expression = cfg.get("expression", "").strip()
            if not expression:
                return None
            # Custom SQL must reference {{prev}} as the upstream table
            return expression.replace("{{prev}}", _q(prev))

        return None

    # ── Sink Writing ──────────────────────────────────────────────────

    async def _write_sink(
        self, conn: Any, final_table: str, sink: PipelineSink, run: PipelineRun
    ) -> None:
        """Write the final table to the configured sink."""
        if sink.type == SinkType.FILE:
            self._write_file_sink(conn, final_table, sink, run)
        elif sink.type == SinkType.POSTGRESQL:
            await self._write_pg_sink(conn, final_table, sink, run)
        elif sink.type == SinkType.DUCKDB:
            self._write_duckdb_sink(conn, final_table, sink, run)
        elif sink.type == SinkType.PREVIEW:
            pass  # preview_data already set
        else:
            raise ValueError(f"Unsupported sink type: {sink.type}")

    def _write_file_sink(
        self, conn: Any, final_table: str, sink: PipelineSink, run: PipelineRun
    ) -> None:
        fmt = (sink.format or "csv").lower()
        base_name = sink.file_name or f"pipeline_output_{run.run_id}"
        # Ensure correct extension
        stem = Path(base_name).stem
        ext_map = {"csv": ".csv", "parquet": ".parquet", "json": ".json"}
        ext = ext_map.get(fmt, ".csv")
        out_name = f"{stem}{ext}"
        out_path = os.path.join(OUTPUT_DIR, out_name)

        if fmt == "csv":
            conn.execute(f"COPY {_q(final_table)} TO '{out_path}' (HEADER, DELIMITER ',')")
        elif fmt == "parquet":
            conn.execute(f"COPY {_q(final_table)} TO '{out_path}' (FORMAT PARQUET)")
        elif fmt == "json":
            conn.execute(f"COPY {_q(final_table)} TO '{out_path}' (FORMAT JSON, ARRAY true)")
        else:
            conn.execute(f"COPY {_q(final_table)} TO '{out_path}' (HEADER, DELIMITER ',')")

        run.output_file = out_name
        logger.info(f"[Pipeline] Wrote {out_name} ({run.rows_written} rows)")

    async def _write_pg_sink(
        self, conn: Any, final_table: str, sink: PipelineSink, run: PipelineRun
    ) -> None:
        """Write DuckDB table to PostgreSQL."""
        from connectors import ConnectorConfig, SourceType as CSourceType, PostgreSQLConnector

        cfg = sink.connection or {}
        table_name = sink.table or "pipeline_output"

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
            # Get data from DuckDB
            result = conn.execute(f"SELECT * FROM {_q(final_table)}")
            col_names = [desc[0] for desc in result.description]
            rows = result.fetchall()

            if not pg.pool:
                raise ConnectionError("PostgreSQL pool not available")

            async with pg.pool.acquire() as pg_conn:
                # Drop + recreate if replace mode
                if sink.if_exists == "replace":
                    await pg_conn.execute(f"DROP TABLE IF EXISTS {table_name}")

                # Build CREATE TABLE from DuckDB column info
                duck_schema = conn.execute(f"DESCRIBE {_q(final_table)}").fetchall()
                pg_type_map = {
                    "INTEGER": "INTEGER", "BIGINT": "BIGINT", "DOUBLE": "DOUBLE PRECISION",
                    "FLOAT": "REAL", "VARCHAR": "TEXT", "BOOLEAN": "BOOLEAN",
                    "DATE": "DATE", "TIMESTAMP": "TIMESTAMP",
                }
                col_defs = []
                for row in duck_schema:
                    duck_type = row[1].upper()
                    pg_type = pg_type_map.get(duck_type, "TEXT")
                    col_defs.append(f'"{row[0]}" {pg_type}')

                if sink.if_exists in ("replace", "fail"):
                    await pg_conn.execute(
                        f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(col_defs)})"
                    )

                # Insert rows in batches
                if rows:
                    placeholders = ", ".join(f"${i+1}" for i in range(len(col_names)))
                    insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"
                    await pg_conn.executemany(insert_sql, rows)

            run.output_table = table_name
            logger.info(f"[Pipeline] Wrote {len(rows)} rows to PostgreSQL: {table_name}")

        finally:
            await pg.disconnect()

    def _write_duckdb_sink(
        self, conn: Any, final_table: str, sink: PipelineSink, run: PipelineRun
    ) -> None:
        table_name = sink.table or "pipeline_output_saved"
        if sink.if_exists == "replace":
            conn.execute(f"DROP TABLE IF EXISTS {_q(table_name)}")
        conn.execute(f"CREATE TABLE {_q(table_name)} AS SELECT * FROM {_q(final_table)}")
        run.output_table = table_name
        logger.info(f"[Pipeline] Wrote to DuckDB table: {table_name}")
