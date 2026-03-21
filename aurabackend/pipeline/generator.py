"""
AI Pipeline Generator
=====================
Converts a natural-language prompt into a fully-typed Pipeline definition
by calling the configured LLM (Gemini / Ollama / OpenAI).

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

from pipeline.models import (
    Pipeline,
    PipelineSource,
    PipelineSink,
    ProcessingStep,
    SourceType,
    SinkType,
    StepType,
    PipelineStatus,
)

logger = logging.getLogger("aura.pipeline.generator")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")

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
4. If the user doesn't specify a sink, default to "preview".
5. If the user doesn't specify a source, infer from context or use the first available file.
6. Keep step descriptions short and clear.
7. Values in filter configs should be strings (they get cast at execution time).
"""


class PipelineGenerator:
    """Generates Pipeline definitions from natural language using LLM."""

    def __init__(self) -> None:
        from shared.llm_provider import get_llm
        self._llm = get_llm()
        logger.info(f"PipelineGenerator using LLM: {self._llm}")

    # ── Public API ────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        available_files: Optional[List[str]] = None,
        schema_context: Optional[Dict[str, Any]] = None,
        connections: Optional[List[Dict[str, str]]] = None,
    ) -> Pipeline:
        """
        Convert a natural-language prompt into a Pipeline object.

        Args:
            prompt:          User's natural language instruction.
            available_files: List of uploaded file names the user can use.
            schema_context:  Column info for available files/tables.
            connections:     List of available database connections.

        Returns:
            A Pipeline object ready for execution.
        """
        context = self._build_context(available_files, schema_context, connections)
        user_message = f"{context}\n\nUser request: {prompt}"

        logger.info(f"[Generator] Generating pipeline for: {prompt[:120]}...")

        raw = self._llm.generate_json([_SYSTEM_PROMPT, user_message])
        if raw is None:
            # Fallback: try plain generate and parse
            text = self._llm.generate([_SYSTEM_PROMPT, user_message])
            if text:
                raw = self._parse_json(text)

        if raw is None:
            raise ValueError("LLM failed to generate a valid pipeline definition")

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
    ) -> str:
        parts: List[str] = []

        # Available files
        files = available_files or self._discover_files()
        if files:
            parts.append(f"Available uploaded files: {', '.join(files)}")

        # Schema info
        if schema_context:
            parts.append(f"Schema information:\n{json.dumps(schema_context, indent=2)}")

        # Database connections
        if connections:
            conn_info = ", ".join(
                f"{c.get('name', 'unnamed')} ({c.get('type', 'unknown')})"
                for c in connections
            )
            parts.append(f"Available database connections: {conn_info}")

        return "\n\n".join(parts) if parts else "No specific context available."

    def _discover_files(self) -> List[str]:
        """List uploaded data files."""
        if not os.path.isdir(UPLOAD_DIR):
            return []
        skip = {".gitkeep", ".DS_Store"}
        data_exts = {".csv", ".parquet", ".json", ".xlsx", ".tsv"}
        files = []
        for f in sorted(os.listdir(UPLOAD_DIR)):
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

    def get_file_schema(self, file_name: str) -> Dict[str, Any]:
        """
        Quick-read column names and types from a file using DuckDB with smart header detection.
        Returns {"columns": [{"name": ..., "type": ...}], "row_count": N, "sample_data": [...]}
        """
        import duckdb
        from shared.data_utils import smart_load_file

        file_path = os.path.join(UPLOAD_DIR, file_name)
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
