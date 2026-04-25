"""
Ingestion Agent
================
Handles: file uploads, DB source connections, data profiling, initial load.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, cast

from agents.base import AgentContext, AgentResult, BaseAgent, Severity
from agents.params import IngestionAgentParams

logger = logging.getLogger("aura.agents.ingestion")


class IngestionAgent(BaseAgent):
    name = "IngestionAgent"
    description = "Ingests files and database sources. Profiles and registers data."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        params = cast(IngestionAgentParams, ctx.metadata or {})
        files = params.get("files", ctx.files)

        if not files and not ctx.connection:
            result.add_step(
                action="detect_sources",
                output_summary="No files or connections provided — checking metadata store",
                severity=Severity.WARNING,
            )
            # Check if there are existing uploads
            if self.tools:
                try:
                    existing = await self.tools.call("list_uploaded_files")
                    if existing:
                        files = existing
                except Exception as exc:
                    logger.exception("list_uploaded_files tool call failed")
                    result.add_step(
                        action="list_uploaded_files",
                        output_summary=f"Tool failed: {exc}",
                        severity=Severity.WARNING,
                    )

        ingested: List[Dict[str, Any]] = []

        # ── File ingestion ──────────────────────────────────────────
        for filepath in (files or []):
            await self._report(f"Ingesting {filepath}…", -1)
            result.add_step(action="ingest_file", input_summary=filepath)

            if self.tools:
                try:
                    profile = await self.tools.call(
                        "ingest_and_profile",
                        filepath=filepath,
                    )
                    ingested.append({
                        "file": filepath,
                        "rows": profile.get("row_count", 0),
                        "columns": profile.get("columns", []),
                        "types": profile.get("column_types", {}),
                        "nulls": profile.get("null_counts", {}),
                    })
                    result.add_step(
                        action="file_profiled",
                        tool_name="ingest_and_profile",
                        output_summary=f"{filepath}: {profile.get('row_count',0)} rows, "
                                       f"{len(profile.get('columns',[]))} columns",
                    )
                except Exception as exc:
                    result.add_step(
                        action="ingest_error",
                        output_summary=str(exc),
                        severity=Severity.ERROR,
                    )

        # ── DB source connection ────────────────────────────────────
        if ctx.connection:
            await self._report("Connecting to database…", -1)
            if self.tools:
                try:
                    schema_info = await self.tools.call(
                        "introspect_database",
                        connection=ctx.connection,
                    )
                    ingested.append({
                        "source": "database",
                        "tables": schema_info.get("tables", []),
                        "connection": ctx.connection.get("database", "unknown"),
                    })
                    result.add_step(
                        action="db_connected",
                        tool_name="introspect_database",
                        output_summary=f"Found {len(schema_info.get('tables',[]))} tables",
                    )
                except Exception as exc:
                    result.add_step(
                        action="db_error",
                        output_summary=str(exc),
                        severity=Severity.ERROR,
                    )

        result.output = {"ingested_sources": ingested, "total_sources": len(ingested)}
        result.artifacts["sources"] = ingested
        return result
