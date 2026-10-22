"""
Ingestion Agent
================
Handles: file uploads, DB source connections, data profiling, initial load.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, cast

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity
from agents.params import IngestionAgentParams
from agents.tools import introspect_args

logger = logging.getLogger("aura.agents.ingestion")


class IngestionAgent(BaseAgent):
    name = "IngestionAgent"
    description = "Ingests files and database sources. Profiles and registers data."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        params = cast(IngestionAgentParams, ctx.metadata or {})
        # BUG-507: parameters.files is written by the planner LLM from the user's
        # prompt, so it can name another tenant's upload. Only ctx.files was confined
        # to the caller's uploads (BUG-441); the plan may narrow it, never extend it.
        files = list(ctx.files or [])
        requested = params.get("files")
        if requested and files:
            wanted = {str(f) for f in requested} | {os.path.basename(str(f)) for f in requested}
            files = [f for f in files if f in wanted or os.path.basename(f) in wanted] or files

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
        errors: List[str] = []

        # ── File ingestion ──────────────────────────────────────────
        for filepath in (files or []):
            await self._report(f"Ingesting {filepath}…", -1)
            result.add_step(action="ingest_file", input_summary=filepath)

            if self.tools:
                try:
                    # The tool's argument is file_path, and the connectors /ingest
                    # route answers rows / dtypes / null_counts (BUG-437: the call used
                    # filepath= and read row_count / column_types, so it always failed).
                    profile = await self.tools.call("ingest_and_profile", file_path=filepath)
                    ingested.append({
                        "file": filepath,
                        "rows": profile.get("rows", 0),
                        "columns": profile.get("columns", []),
                        "types": profile.get("dtypes", {}),
                        "nulls": profile.get("null_counts", {}),
                    })
                    result.add_step(
                        action="file_profiled",
                        tool_name="ingest_and_profile",
                        output_summary=f"{filepath}: {profile.get('rows', 0)} rows, "
                                       f"{len(profile.get('columns', []))} columns",
                    )
                except Exception as exc:
                    errors.append(f"{filepath}: {exc}")
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
                        "introspect_database", **introspect_args(ctx.connection),
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
                    errors.append(f"database: {exc}")
                    result.add_step(
                        action="db_error",
                        output_summary=str(exc),
                        severity=Severity.ERROR,
                    )

        result.output = {"ingested_sources": ingested, "total_sources": len(ingested)}
        result.artifacts["sources"] = ingested
        # BUG-437: every source failing used to leave the status unset, which the base
        # class turns into SUCCESS -- the plan reported a completed ingest with zero
        # sources and ran its downstream tasks on nothing.
        if errors and not ingested:
            result.status = AgentStatus.FAILED
            result.error = "No source could be ingested: " + "; ".join(errors)
        else:
            # Some sources may have failed; what was ingested is still a result.
            result.status = AgentStatus.SUCCESS
        return result
