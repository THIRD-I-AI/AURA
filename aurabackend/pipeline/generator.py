"""
AI Pipeline Generator
=====================
Hybrid NLP → Pipeline engine with three-tier fallback:

  1. **Local rule-based parser** — instant, no LLM, no API key, no token limits.
     Handles ~80 % of common ETL requests deterministically.
  2. **Ollama local model** — for complex prompts the rule parser can't handle.
     Uses self-hosted SQL-specific models (sqlcoder / duckdb-nsql).
  3. **Cloud LLM** (Groq / Gemini / OpenAI) — last resort fallback.

Usage:
    from pipeline.generator import PipelineGenerator
    gen = PipelineGenerator()
    pipeline = await gen.generate("Filter products with rating > 4, sort by price desc, export as CSV")
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.engine import _resolve_in_dir
from pipeline.local_parser import LocalPipelineParser
from pipeline.models import (
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)
from shared.llm_provider import LLMRateLimitError

logger = logging.getLogger("aura.pipeline.generator")

# Fallback for callers that pass no upload_dir (tests, CLI use). Real
# HTTP requests get a tenant-scoped dir passed in by the router (see
# get_file_schema()/generate()'s upload_dir param) — mirrors the same
# fallback in pipeline/engine.py.
UPLOAD_DIR = os.getenv("AURA_UPLOADS_ROOT") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "uploads"
)

# ────────────────────────────────────────────────────────────────────
# System prompt template — injected with available context
# ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are AURA, an AI data pipeline architect.

Given a user request, produce a JSON pipeline definition following this EXACT schema.

## Available Source Types
- "file"       — uploaded file (CSV / Parquet / JSON)
- "postgresql" — PostgreSQL database table
- "mysql"      — MySQL database table
- "duckdb"     — DuckDB table

## Available Sink Types
- "file"    — export to CSV / Parquet / JSON
- "preview" — return rows without writing (default if not specified)
- "postgresql" — write to PostgreSQL table
- "duckdb"     — write to DuckDB table

## Available Step Types (processing)
- "filter"          — config: {column, operator, value}  operators: =, !=, >, <, >=, <=, LIKE, IN, IS NULL, IS NOT NULL
- "sort"            — config: {column, direction}  direction: ASC or DESC
- "drop_columns"    — config: {columns: [list]}
- "rename_columns"  — config: {mapping: {old: new, ...}}
- "add_column"      — config: {name, expression}  expression is SQL
- "cast_type"       — config: {column, new_type}  types: INTEGER, VARCHAR, DOUBLE, BOOLEAN, DATE, TIMESTAMP
- "fill_missing"    — config: {column, strategy, fill_value}  strategy: value, mean, median
- "deduplicate"     — config: {columns: [list]}  empty = all columns
- "aggregate"       — config: {group_by: [cols], aggregations: [{function, column, alias}]}
- "join"            — config: {join_type, left_key, right_key, right_table}
- "window"          — config: {function, partition_by: [cols], order_by, alias}
- "pivot"           — config: {values_column, pivot_column, agg_function}
- "unpivot"         — config: {columns: [list]}
- "limit"           — config: {count: N}
- "custom_sql"      — config: {expression}  must use {{prev}} for upstream table
- "union"           — (future, skip for now)

## Output JSON Schema
```json
{
  "name": "short pipeline name",
  "description": "what this pipeline does",
  "source": {
    "type": "file",
    "file_name": "products.csv"
  },
  "steps": [
    {
      "type": "filter",
      "description": "Keep only high-rated items",
      "config": {"column": "rating", "operator": ">", "value": "4"}
    }
  ],
  "sink": {
    "type": "preview",
    "format": "csv"
  },
  "tags": ["generated"]
}
```

RULES:
1. Output ONLY valid JSON — no markdown, no explanation, no code fences.
2. Every step MUST have type, description, and config.
3. Use the EXACT column names from the schema context provided.
4. Always enclose ALL table and column names in double quotes (e.g., "my_table"."my_column") to ensure compatibility with identifiers containing special characters like '&', spaces, or reserved keywords.
5. If the user doesn't specify a sink, default to "preview".
6. If the user doesn't specify a source, infer from context or use the first available file.
7. Keep step descriptions short and clear.
8. Values in filter configs should be strings (they get cast at execution time).
"""


class PipelineGenerator:
    """Generates Pipeline definitions from natural language using a three-tier approach."""

    # Minimum confidence score from the local parser to skip LLM entirely
    LOCAL_CONFIDENCE_THRESHOLD = 0.5

    def __init__(self) -> None:
        from shared.llm_provider import get_llm
        self._llm = get_llm()
        self._local_parser = LocalPipelineParser()
        logger.info(f"PipelineGenerator ready — local parser + LLM ({self._llm})")

    # ── Public API ────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        available_files: Optional[List[str]] = None,
        schema_context: Optional[Dict[str, Any]] = None,
        connections: Optional[List[Dict[str, str]]] = None,
        upload_dir: Optional[str] = None,
    ) -> Pipeline:
        """
        Convert a natural-language prompt into a Pipeline object.

        Resolution order:
          1. Local rule-based parser (instant, no LLM)
          2. Cloud/local LLM fallback (Groq → Gemini → Ollama → OpenAI)

        ``upload_dir``: the caller's resolved (tenant-scoped) upload
        directory — e.g. ``api_gateway.routers.workspaces.tenant_upload_dir(request)``.
        Only consulted when ``available_files`` isn't already supplied;
        callers with no request context (CLI, tests) may omit it.
        """
        # ── Tier 1: Local rule-based parser ──────────────────────────
        try:
            source_file = None
            if available_files:
                source_file = available_files[0]
            elif schema_context:
                source_file = next(iter(schema_context), None)

            result = self._local_parser.parse(
                prompt,
                schema_context=schema_context,
                available_files=available_files or self._discover_files(upload_dir),
                source_file=source_file,
            )
            if result is not None:
                pipeline, confidence = result
                logger.info(
                    "[Generator] Local parser succeeded (confidence=%.2f, steps=%d)",
                    confidence, len(pipeline.steps),
                )
                if confidence >= self.LOCAL_CONFIDENCE_THRESHOLD:
                    return pipeline
                # Low confidence — we'll try LLM for a better result,
                # but keep this as a fallback
                local_fallback = pipeline
            else:
                local_fallback = None
        except Exception as e:
            logger.warning("[Generator] Local parser error: %s", e)
            local_fallback = None

        # ── Tier 2: LLM-based generation ─────────────────────────────
        context = self._build_context(available_files, schema_context, connections, upload_dir)
        user_message = f"{context}\n\nUser request: {prompt}"
        logger.info(f"[Generator] Falling back to LLM for: {prompt[:120]}...")

        try:
            raw = self._llm.generate_json([_SYSTEM_PROMPT, user_message])
            if raw is None:
                text = self._llm.generate([_SYSTEM_PROMPT, user_message])
                if text:
                    raw = self._parse_json(text)
        except LLMRateLimitError as e:
            # If we have a local result, use it instead of failing
            if local_fallback is not None:
                logger.info("[Generator] LLM rate-limited — using local parser result")
                return local_fallback
            raise ValueError(
                f"Prompt too large for the LLM provider. "
                f"Try a shorter prompt or select a specific source file. ({e})"
            )

        if raw is None:
            # Return local fallback if available
            if local_fallback is not None:
                logger.info("[Generator] LLM returned no result — using local parser result")
                return local_fallback
            if not self._llm.is_available():
                raise ValueError(
                    "No LLM provider available. Check that GROQ_API_KEY (or another provider key) is set."
                )
            raise ValueError(
                "LLM returned an unparseable response. Try rephrasing your pipeline description."
            )

        pipeline = self._parse_pipeline(raw, prompt)
        logger.info(
            f"[Generator] Created pipeline '{pipeline.name}' with "
            f"{len(pipeline.steps)} steps: {pipeline.source.type.value} → {pipeline.sink.type.value}"
        )
        return pipeline

    async def suggest_steps(
        self,
        prompt: str,
        schema_context: Optional[Dict[str, Any]] = None,
    ) -> List[ProcessingStep]:
        """Generate just the processing steps (not full pipeline) from a prompt."""
        context = ""
        if schema_context:
            context = f"\nAvailable columns:\n{json.dumps(schema_context, indent=2)}\n"

        user_msg = (
            f"{context}\n\nGenerate ONLY the 'steps' array for this request: {prompt}\n"
            "Output a JSON array of step objects."
        )

        raw = self._llm.generate_json([_SYSTEM_PROMPT, user_msg])
        if raw is None:
            text = self._llm.generate([_SYSTEM_PROMPT, user_msg])
            if text:
                raw = self._parse_json(text)

        if raw is None:
            return []

        # Handle both array and object with "steps" key
        steps_data = raw if isinstance(raw, list) else raw.get("steps", [])
        return [self._parse_step(s) for s in steps_data if isinstance(s, dict)]

    # ── Context Building ──────────────────────────────────────────────

    def _build_context(
        self,
        available_files: Optional[List[str]],
        schema_context: Optional[Dict[str, Any]],
        connections: Optional[List[Dict[str, str]]],
        upload_dir: Optional[str] = None,
    ) -> str:
        parts: List[str] = []

        # Available files
        files = available_files or self._discover_files(upload_dir)
        if files:
            parts.append(f"Available uploaded files: {', '.join(files)}")

        # Schema info (truncated to keep prompt within LLM token limits)
        if schema_context:
            compact = {}
            for fname, info in schema_context.items():
                cols = info.get("columns", [])
                sample = info.get("sample_data", [])[:3]  # max 3 sample rows
                # Truncate long string values in sample data
                trimmed_sample = []
                for row in sample:
                    trimmed_sample.append(
                        {k: (str(v)[:80] if isinstance(v, str) and len(str(v)) > 80 else v)
                         for k, v in (row.items() if isinstance(row, dict) else {})}
                    )
                compact[fname] = {
                    "columns": cols,
                    "row_count": info.get("row_count", 0),
                    "sample_data": trimmed_sample,
                }
            schema_json = json.dumps(compact, indent=2, default=str)
            # Hard cap to avoid blowing through token limits
            if len(schema_json) > 4000:
                # Fall back to columns-only (no sample data)
                for fname in compact:
                    compact[fname].pop("sample_data", None)
                schema_json = json.dumps(compact, indent=2, default=str)
            parts.append(f"Schema information:\n{schema_json}")

        # Database connections
        if connections:
            conn_info = ", ".join(
                f"{c.get('name', 'unnamed')} ({c.get('type', 'unknown')})"
                for c in connections
            )
            parts.append(f"Available database connections: {conn_info}")

        return "\n\n".join(parts) if parts else "No specific context available."

    def _discover_files(self, upload_dir: Optional[str] = None) -> List[str]:
        """List uploaded data files in the caller's (tenant-scoped) upload dir."""
        base_dir = upload_dir or UPLOAD_DIR
        if not os.path.isdir(base_dir):
            return []
        skip = {".gitkeep", ".DS_Store"}
        data_exts = {".csv", ".parquet", ".json", ".xlsx", ".tsv"}
        files = []
        for f in sorted(os.listdir(base_dir)):
            if f in skip:
                continue
            if Path(f).suffix.lower() in data_exts:
                files.append(f)
        return files

    # ── Parsing Helpers ───────────────────────────────────────────────

    def _parse_json(self, text: str) -> Optional[Any]:
        """Try to extract JSON from LLM output."""
        cleaned = text.strip()
        # Strip code fences
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None

    def _parse_pipeline(self, data: Dict[str, Any], original_prompt: str) -> Pipeline:
        """Convert raw JSON dict into a typed Pipeline object."""
        # Source
        source_data = data.get("source", {})
        source = PipelineSource(
            type=SourceType(source_data.get("type", "file")),
            file_name=source_data.get("file_name"),
            connection=source_data.get("connection"),
            table=source_data.get("table"),
            query=source_data.get("query"),
        )

        # Sink
        sink_data = data.get("sink", {"type": "preview"})
        sink = PipelineSink(
            type=SinkType(sink_data.get("type", "preview")),
            format=sink_data.get("format", "csv"),
            file_name=sink_data.get("file_name"),
            connection=sink_data.get("connection"),
            table=sink_data.get("table"),
            if_exists=sink_data.get("if_exists", "replace"),
        )

        # Steps
        steps = [self._parse_step(s) for s in data.get("steps", []) if isinstance(s, dict)]

        # Tags
        tags = data.get("tags", ["ai-generated"])
        if "ai-generated" not in tags:
            tags.append("ai-generated")

        return Pipeline(
            name=data.get("name", "AI Generated Pipeline"),
            description=data.get("description", ""),
            source=source,
            steps=steps,
            sink=sink,
            status=PipelineStatus.READY,
            generated_from_prompt=original_prompt,
            tags=tags,
        )

    def _parse_step(self, data: Dict[str, Any]) -> ProcessingStep:
        """Parse a single processing step from JSON."""
        step_type_str = data.get("type", "")
        try:
            step_type = StepType(step_type_str)
        except ValueError:
            step_type = StepType.CUSTOM_SQL

        return ProcessingStep(
            type=step_type,
            description=data.get("description", ""),
            config=data.get("config", {}),
        )

    # ── Schema Discovery ──────────────────────────────────────────────

    def get_file_schema(self, file_name: str, upload_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Quick-read column names and types from a file using DuckDB with smart header detection.
        Returns {"columns": [{"name": ..., "type": ...}], "row_count": N, "sample_data": [...]}

        ``upload_dir``: the caller's resolved (tenant-scoped) upload
        directory. Callers with no request context (CLI, tests) may
        omit it and fall back to the module-level UPLOAD_DIR.
        ``file_name`` is user input (pipeline definition / request body),
        so it's resolved via the same basename + containment check the
        engine uses for FILE sources (pipeline.engine._resolve_in_dir).
        """
        import duckdb

        from shared.data_utils import smart_load_file

        try:
            file_path = _resolve_in_dir(upload_dir or UPLOAD_DIR, file_name)
        except ValueError:
            return {"error": f"Invalid file name: {file_name}"}
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_name}"}

        table_name = Path(file_name).stem.replace("-", "_").replace(" ", "_")
        conn = duckdb.connect(":memory:")
        try:
            info = smart_load_file(conn, file_path, table_name, use_llm=True)
            return {
                "file_name": file_name,
                "columns": info["columns"],
                "row_count": info["row_count"],
                "sample_data": info.get("sample_data", []),
                "headers_inferred": info.get("headers_inferred", False),
            }
        finally:
            conn.close()
