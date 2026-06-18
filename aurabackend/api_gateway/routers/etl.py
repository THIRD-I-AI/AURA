"""
ETL Router
===========
ETL pipeline endpoints: preview source, execute transforms, natural language,
and file download.
"""

import decimal
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from shared.error_handler import sanitize_error
from shared.logging_config import get_logger
from shared.safe_paths import PathTraversalError, safe_join
from shared.streaming_manager import TOPIC_ETL, streaming_manager

from .workspaces import tenant_upload_dir

logger = get_logger("aura.api_gateway.etl")

router = APIRouter(tags=["ETL"])


# ── Models ───────────────────────────────────────────────────────────

class ETLTransformStep(BaseModel):
    id: str = ""
    type: str = Field(..., description="Transform type")
    description: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)


class ETLPipelineRequest(BaseModel):
    name: str = "Untitled Pipeline"
    source_file: str = Field(..., description="Uploaded filename to use as source")
    destination_format: str = Field(default="csv", description="Output format: csv, parquet, json")
    destination_filename: Optional[str] = None
    transforms: List[ETLTransformStep] = Field(default_factory=list)
    preview_only: bool = False


class ETLNaturalLanguageRequest(BaseModel):
    source_file: str
    instruction: str
    destination_format: str = "csv"


# ── Helpers ──────────────────────────────────────────────────────────

def _serialize_value(val: Any) -> Any:
    """Make DuckDB values JSON-serializable."""
    if val is None:
        return None
    if isinstance(val, decimal.Decimal):
        if val == int(val):
            return int(val)
        return float(val)
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        if val == int(val) and abs(val) < 2**53:
            return int(val)
        return round(val, 10)
    if isinstance(val, (datetime,)):
        return val.isoformat()
    if hasattr(val, 'isoformat'):
        return val.isoformat()
    return val


def _q(name: str) -> str:
    """Double-quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def _build_transform_sql(table: str, steps: List[ETLTransformStep], con=None) -> str:
    """Convert a list of transform steps into a single DuckDB SQL pipeline."""
    if not steps:
        return f"SELECT * FROM {_q(table)}"

    cte_parts: List[str] = []
    prev = table
    skipped = 0

    for i, step in enumerate(steps):
        alias = f"step_{i}"
        cfg = step.config or {}
        t = step.type

        if t == "filter":
            condition = (cfg.get("condition") or "").strip()
            if not condition:
                skipped += 1
                continue
            cte_parts.append(f"{alias} AS (SELECT * FROM {_q(prev)} WHERE {condition})")

        elif t == "rename":
            mappings = cfg.get("mappings") or {}
            valid = {old: new for old, new in mappings.items() if old and new}
            if not valid:
                skipped += 1
                continue
            renames = [f'"{old}" AS "{new}"' for old, new in valid.items()]
            cte_parts.append(f'{alias} AS (SELECT * RENAME ({", ".join(renames)}) FROM {_q(prev)})')

        elif t == "drop_columns":
            cols = [c for c in (cfg.get("columns") or []) if c]
            if not cols:
                skipped += 1
                continue
            excludes = ", ".join(f'"{c}"' for c in cols)
            cte_parts.append(f"{alias} AS (SELECT * EXCLUDE ({excludes}) FROM {_q(prev)})")

        elif t == "add_column":
            expr = (cfg.get("expression") or "").strip()
            col_name = (cfg.get("name") or "").strip()
            if not expr or not col_name:
                skipped += 1
                continue
            cte_parts.append(f'{alias} AS (SELECT *, ({expr}) AS "{col_name}" FROM {_q(prev)})')

        elif t == "sort":
            col = (cfg.get("column") or "").strip()
            if not col:
                skipped += 1
                continue
            order = cfg.get("order", "ASC").upper()
            if order not in ("ASC", "DESC"):
                order = "ASC"
            cte_parts.append(f'{alias} AS (SELECT * FROM {_q(prev)} ORDER BY "{col}" {order})')

        elif t == "aggregate":
            group_by = [c for c in (cfg.get("group_by") or []) if c]
            agg_exprs = cfg.get("aggregations") or []
            valid_aggs = [a for a in agg_exprs if a.get("column") and a.get("func")]
            if not group_by or not valid_aggs:
                skipped += 1
                continue
            g = ", ".join(f'"{c}"' for c in group_by)
            a = ", ".join(f'{agg["func"]}("{agg["column"]}") AS "{agg.get("alias", agg["column"])}"' for agg in valid_aggs)
            cte_parts.append(f"{alias} AS (SELECT {g}, {a} FROM {_q(prev)} GROUP BY {g})")

        elif t == "deduplicate":
            cols = [c for c in (cfg.get("columns") or []) if c]
            if cols:
                partition = ", ".join(f'"{c}"' for c in cols)
                cte_parts.append(
                    f"{alias} AS (SELECT * FROM (SELECT *, ROW_NUMBER() OVER "
                    f"(PARTITION BY {partition}) AS _rn FROM {_q(prev)}) WHERE _rn = 1)"
                )
            else:
                cte_parts.append(f"{alias} AS (SELECT DISTINCT * FROM {_q(prev)})")

        elif t == "cast_type":
            col = (cfg.get("column") or "").strip()
            to_type = (cfg.get("to_type") or "").strip()
            if not col or not to_type:
                skipped += 1
                continue
            cte_parts.append(f'{alias} AS (SELECT * REPLACE (CAST("{col}" AS {to_type}) AS "{col}") FROM {_q(prev)})')

        elif t == "fill_missing":
            col = (cfg.get("column") or "").strip()
            fill_val = (cfg.get("value") or "").strip()
            strategy = (cfg.get("strategy") or "value").strip().lower()

            if col == "*" and con is not None:
                src = _q(prev) if cte_parts else _q(table)
                try:
                    schema = con.execute(f'DESCRIBE (SELECT * FROM {src})').fetchall() if cte_parts else con.execute(f'DESCRIBE {_q(table)}').fetchall()
                except Exception:
                    schema = con.execute(f'DESCRIBE {_q(table)}').fetchall()

                _val_is_numeric = False
                if fill_val:
                    try:
                        float(fill_val)
                        _val_is_numeric = True
                    except ValueError:
                        pass

                try:
                    null_count_exprs = ", ".join(f'SUM(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END) AS "{c}"' for c, *_ in schema)
                    null_row = con.execute(f'SELECT {null_count_exprs} FROM {src}').fetchone()
                    cols_with_nulls = {schema[j][0] for j, cnt in enumerate(null_row) if cnt and cnt > 0}
                except Exception:
                    cols_with_nulls = None

                replaces = []
                for c_name, c_type, *_ in schema:
                    if cols_with_nulls is not None and c_name not in cols_with_nulls:
                        continue
                    is_numeric = any(t in c_type.upper() for t in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "BIGINT", "SMALLINT", "TINYINT", "REAL"))
                    if strategy == "mean" and is_numeric:
                        replaces.append(f'COALESCE("{c_name}", AVG("{c_name}") OVER ()) AS "{c_name}"')
                    elif strategy == "median" and is_numeric:
                        replaces.append(f'COALESCE("{c_name}", MEDIAN("{c_name}") OVER ()) AS "{c_name}"')
                    elif strategy in ("mean", "median") and not is_numeric:
                        if fill_val and not _val_is_numeric:
                            safe = fill_val.replace("'", "''")
                            replaces.append(f"COALESCE(\"{c_name}\", '{safe}') AS \"{c_name}\"")
                    elif is_numeric and fill_val:
                        replaces.append(f'COALESCE("{c_name}", {fill_val}) AS "{c_name}"')
                    elif is_numeric and not fill_val:
                        replaces.append(f'COALESCE("{c_name}", 0) AS "{c_name}"')
                    elif not is_numeric and fill_val and not _val_is_numeric:
                        safe = fill_val.replace("'", "''")
                        replaces.append(f"COALESCE(\"{c_name}\", '{safe}') AS \"{c_name}\"")
                if replaces:
                    cte_parts.append(f'{alias} AS (SELECT * REPLACE ({", ".join(replaces)}) FROM {_q(prev)})')
                else:
                    skipped += 1
                    continue
            elif not col or not fill_val:
                skipped += 1
                continue
            else:
                cte_parts.append(f'{alias} AS (SELECT * REPLACE (COALESCE("{col}", {fill_val}) AS "{col}") FROM {_q(prev)})')

        elif t == "custom_sql":
            sql_expr = (cfg.get("sql") or "").strip()
            if not sql_expr:
                skipped += 1
                continue
            sql_expr = sql_expr.replace("{{input}}", _q(prev))
            cte_parts.append(f"{alias} AS ({sql_expr})")

        else:
            skipped += 1
            continue

        prev = alias

    if skipped:
        logger.info("ETL pipeline: %d step(s) skipped due to empty config", skipped)

    if cte_parts:
        return "WITH " + ",\n".join(cte_parts) + f"\nSELECT * FROM {_q(prev)}"
    return f"SELECT * FROM {_q(table)}"


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/etl/preview-source")
async def etl_preview_source(payload: Dict[str, Any], request: Request):
    """Preview the schema + first N rows of a source file."""
    import duckdb

    from shared.data_utils import smart_load_file

    source_file = payload.get("source_file", "")
    limit = payload.get("limit", 20)
    upload_dirs = [Path(tenant_upload_dir(request))]

    file_path = None
    for d in upload_dirs:
        candidate = d / source_file
        if candidate.exists():
            file_path = str(candidate)
            break

    if not file_path:
        raise HTTPException(status_code=404, detail=f"Source file '{source_file}' not found in uploads")

    try:
        con = duckdb.connect(":memory:")
        table_name = re.sub(r"[^A-Za-z0-9_]", "_", Path(source_file).stem)
        file_info = smart_load_file(con, file_path, table_name, use_llm=True)

        columns = file_info["columns"]
        row_count = file_info["row_count"]
        col_names = [c["name"] for c in columns]

        preview = con.execute(f'SELECT * FROM "{table_name}" LIMIT {limit}').fetchall()
        preview_records = [
            {col: _serialize_value(val) for col, val in zip(col_names, row)}
            for row in preview
        ]
        con.close()
        return {
            "status": "success", "source_file": source_file, "table_name": table_name,
            "columns": columns, "row_count": row_count, "preview": preview_records,
            "headers_inferred": file_info.get("headers_inferred", False),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="etl preview")}


@router.post("/etl/execute")
async def etl_execute(pipeline: ETLPipelineRequest, request: Request):
    """Execute an ETL pipeline: load source → apply transforms → write destination."""
    import duckdb

    from shared.data_utils import smart_load_file

    logger.info("ETL execute: pipeline='%s' source='%s' transforms=%d preview_only=%s", pipeline.name, pipeline.source_file, len(pipeline.transforms), pipeline.preview_only)

    run_id = f"etl-{int(time.time()*1000)}"
    await streaming_manager.publish_progress(TOPIC_ETL, run_id, f"Starting ETL pipeline '{pipeline.name}'", 0.05)

    t0 = time.perf_counter()
    base = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    upload_dir = Path(tenant_upload_dir(request))
    output_dir = base / "data" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Sec-2 #36: user-supplied source_file must be sandboxed under upload_dir.
    try:
        file_path = safe_join(upload_dir, pipeline.source_file)
    except PathTraversalError:
        raise HTTPException(status_code=400, detail="Invalid source filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Source file not found")

    try:
        con = duckdb.connect(":memory:")
        table_name = re.sub(r"[^A-Za-z0-9_]", "_", Path(pipeline.source_file).stem)
        await streaming_manager.publish_progress(TOPIC_ETL, run_id, f"Loading source file '{pipeline.source_file}'", 0.2)
        file_info = smart_load_file(con, str(file_path), table_name, use_llm=True)
        source_count = file_info["row_count"]
        source_columns = file_info["columns"]

        await streaming_manager.publish_progress(TOPIC_ETL, run_id, f"Applying {len(pipeline.transforms)} transform(s)", 0.5)
        transform_sql = _build_transform_sql(table_name, pipeline.transforms, con=con)
        con.execute(f"CREATE TABLE _etl_output AS {transform_sql}")

        output_count = con.execute("SELECT COUNT(*) FROM _etl_output").fetchone()[0]
        output_schema = con.execute("DESCRIBE _etl_output").fetchall()
        output_columns = [{"name": r[0], "type": r[1]} for r in output_schema]

        preview_result = con.execute("SELECT * FROM _etl_output LIMIT 50").fetchall()
        col_names = [c["name"] for c in output_columns]
        preview_records = [
            {col: _serialize_value(val) for col, val in zip(col_names, row)}
            for row in preview_result
        ]

        output_path = None
        download_filename = None

        if not pipeline.preview_only:
            raw_dest = pipeline.destination_filename or f"{table_name}_transformed"
            dest_name = Path(raw_dest).stem
            fmt = pipeline.destination_format.lower()

            if fmt == "csv":
                download_filename = f"{dest_name}.csv"
                output_path = str(output_dir / download_filename)
                con.execute(f"COPY _etl_output TO '{output_path}' (HEADER, DELIMITER ',')")
            elif fmt == "parquet":
                download_filename = f"{dest_name}.parquet"
                output_path = str(output_dir / download_filename)
                con.execute(f"COPY _etl_output TO '{output_path}' (FORMAT PARQUET)")
            elif fmt == "json":
                download_filename = f"{dest_name}.json"
                output_path = str(output_dir / download_filename)
                con.execute(f"COPY _etl_output TO '{output_path}' (FORMAT JSON, ARRAY true)")
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported destination format: {fmt}")

        con.close()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        await streaming_manager.publish_complete(TOPIC_ETL, run_id, {
            "pipeline_name": pipeline.name,
            "source_rows": source_count,
            "output_rows": output_count,
            "execution_time_ms": round(elapsed_ms, 1),
        })

        return {
            "status": "success", "pipeline_name": pipeline.name, "run_id": run_id,
            "source": {"file": pipeline.source_file, "row_count": source_count, "columns": source_columns},
            "output": {"row_count": output_count, "columns": output_columns, "file": download_filename, "format": pipeline.destination_format},
            "transform_sql": transform_sql, "transforms_applied": len(pipeline.transforms),
            "preview": preview_records, "execution_time_ms": round(elapsed_ms, 1),
            "preview_only": pipeline.preview_only,
        }
    except HTTPException:
        raise
    except Exception as e:
        # Sec-2 #16-#19: don't echo raw exception text to client OR
        # SSE stream — log + send a sanitized message. The full
        # traceback is in the server log under "etl execute".
        safe_message = sanitize_error(e, logger=logger, context=f"etl execute pipeline={pipeline.name!r}")
        await streaming_manager.publish_error(TOPIC_ETL, run_id, safe_message)
        return {"status": "error", "run_id": run_id, "error": safe_message, "transform_sql": "", "preview": []}


@router.get("/etl/download/{filename}")
async def etl_download(filename: str):
    """Download a processed ETL output file."""
    base = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    output_dir = base / "data" / "processed"

    # Sec-2 #37-#38: inline sanitizer at the FileResponse sink. The
    # `realpath + startswith` pattern is the canonical CodeQL
    # py/path-injection sanitizer that the standard query model
    # recognises directly (no intermediate variable, comparison in
    # the same `if`).
    if (not filename) or os.path.isabs(filename) or any(p == ".." for p in Path(filename).parts):
        raise HTTPException(status_code=400, detail="Invalid filename")
    output_dir_real = os.path.realpath(str(output_dir)) + os.sep
    file_path_str = os.path.realpath(os.path.join(output_dir_real, filename))
    if not file_path_str.startswith(output_dir_real):
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = Path(file_path_str)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    media_types = {".csv": "text/csv", ".parquet": "application/octet-stream", ".json": "application/json"}
    media_type = media_types.get(file_path.suffix.lower(), "application/octet-stream")
    return FileResponse(path=file_path_str, filename=file_path.name, media_type=media_type)


@router.post("/etl/natural-language")
async def etl_from_natural_language(req: ETLNaturalLanguageRequest, request: Request):
    """Use LLM to build transform steps from a natural language instruction."""
    import duckdb

    from shared.data_utils import smart_load_file
    from shared.llm_provider import get_llm

    upload_dir = Path(tenant_upload_dir(request))
    # Sec-2 #39: source_file is user-supplied; sandbox under upload_dir.
    try:
        file_path = safe_join(upload_dir, req.source_file)
    except PathTraversalError:
        raise HTTPException(status_code=400, detail="Invalid source filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Source file not found")

    try:
        con = duckdb.connect(":memory:")
        table_name = re.sub(r"[^A-Za-z0-9_]", "_", Path(req.source_file).stem)
        file_info = smart_load_file(con, str(file_path), table_name, use_llm=True)
        schema_rows = [(c["name"], c["type"]) for c in file_info["columns"]]
        col_names = [c["name"] for c in file_info["columns"]]
        sample = con.execute(f'SELECT * FROM "{table_name}" LIMIT 5').fetchall()
        sample_records = [dict(zip(col_names, row)) for row in sample]
        con.close()
        schema_text = ", ".join(f"{r[0]} ({r[1]})" for r in schema_rows)
    except Exception as e:
        # Sec-3 #18: f"{e}" leaks server-side paths + duckdb internals
        # straight into the response body. sanitize_error logs the full
        # detail server-side and returns the curated message for
        # AuraError subclasses / a generic fallback otherwise.
        return {
            "status": "error",
            "error": sanitize_error(
                e, logger=logger,
                context="etl suggest source-read",
                fallback="Failed to read source",
            ),
            "transforms": [],
        }

    llm = get_llm()

    prompt = f"""You are a data transformation expert. Given a source table schema and user instruction,
generate a list of ETL transform steps as JSON.

SOURCE TABLE: {table_name}
COLUMNS: {schema_text}
SAMPLE DATA (first 5 rows): {json.dumps(sample_records[:3], default=str)}

USER INSTRUCTION: {req.instruction}

IMPORTANT: Always enclose ALL table and column names in double quotes.

Return ONLY a JSON array of transform step objects. Each step has:
- "type": one of "filter", "rename", "drop_columns", "add_column", "sort", "aggregate", "deduplicate", "cast_type", "fill_missing", "custom_sql"
- "description": what this step does
- "config": configuration object specific to the step type

Return ONLY the JSON array, no markdown, no explanation."""

    transforms = []
    llm_error = None
    if llm.is_available():
        try:
            parsed = llm.generate_json(prompt)
            if isinstance(parsed, list):
                transforms = parsed
            elif isinstance(parsed, dict) and "transforms" in parsed:
                transforms = parsed["transforms"]
        except Exception as e:
            # Sec-2 #18: llm_error flows through to a client-visible
            # `error_message` field below; never put the raw LLM
            # provider exception there (it can contain API keys in
            # the traceback formatting).
            llm_error = sanitize_error(e, logger=logger, context="etl natural-language LLM call")
    else:
        llm_error = "No LLM provider available"

    if not transforms:
        if llm_error:
            return {"status": "error", "error": f"LLM failed: {llm_error}", "source_file": req.source_file, "instruction": req.instruction, "transforms": [], "schema": [{"name": r[0], "type": r[1]} for r in schema_rows]}
        transforms = [{"type": "custom_sql", "description": req.instruction, "config": {"sql": "SELECT * FROM {{input}}"}}]

    return {"status": "success", "source_file": req.source_file, "instruction": req.instruction, "transforms": transforms, "schema": [{"name": r[0], "type": r[1]} for r in schema_rows]}
