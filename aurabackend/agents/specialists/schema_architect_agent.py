"""
Schema Architect Agent
=======================
Handles: schema inspection, table creation, ALTER statements, index recommendations.
"""
from __future__ import annotations

from typing import Any, Dict, List

from agents.base import AgentContext, AgentResult, BaseAgent, Severity


class SchemaArchitectAgent(BaseAgent):
    name = "SchemaArchitectAgent"
    description = "Designs, inspects, and modifies database schemas."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Inspecting schema…", 10)

        # Use upstream ingestion results if available
        sources = ctx.upstream_results.get("IngestionAgent", {}).get("ingested_sources", [])

        tables_discovered: List[Dict[str, Any]] = []

        # ── Inspect existing schema ─────────────────────────────────
        if self.tools and ctx.connection:
            try:
                schema = await self.tools.call("introspect_database", connection=ctx.connection)
                tables_discovered = schema.get("tables", [])
                result.add_step(
                    action="schema_inspected",
                    tool_name="introspect_database",
                    output_summary=f"Found {len(tables_discovered)} existing tables",
                )
            except Exception as exc:
                result.add_step(action="schema_error", output_summary=str(exc), severity=Severity.ERROR)

        # ── Generate CREATE TABLE for ingested files ────────────────
        ddl_statements: List[str] = []
        for source in sources:
            if "columns" in source and "types" in source:
                table_name = self._derive_table_name(source.get("file", "data"))
                ddl = self._generate_create_table(table_name, source["columns"], source["types"])
                ddl_statements.append(ddl)
                result.add_step(
                    action="ddl_generated",
                    output_summary=f"CREATE TABLE {table_name} ({len(source['columns'])} columns)",
                )

        await self._report("Recommending indexes…", 60)

        # ── Index recommendations ───────────────────────────────────
        index_recs: List[str] = []
        if self.tools:
            try:
                recs = await self.tools.call(
                    "recommend_indexes",
                    schema_context=ctx.schema_context,
                    tables=tables_discovered,
                )
                index_recs = recs if isinstance(recs, list) else []
            except Exception:
                pass

        result.output = {
            "tables_discovered": len(tables_discovered),
            "ddl_statements": ddl_statements,
            "index_recommendations": index_recs,
        }
        result.artifacts["ddl"] = ddl_statements
        result.artifacts["tables"] = tables_discovered
        return result

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _derive_table_name(filepath: str) -> str:
        import os
        name = os.path.splitext(os.path.basename(filepath))[0]
        # sanitize: lowercase, replace non-alnum with _
        clean = "".join(c if c.isalnum() else "_" for c in name.lower())
        return clean.strip("_") or "imported_data"

    @staticmethod
    def _generate_create_table(
        table_name: str,
        columns: List[str],
        col_types: Dict[str, str],
    ) -> str:
        type_map = {
            "int64": "BIGINT",
            "float64": "DOUBLE PRECISION",
            "object": "TEXT",
            "bool": "BOOLEAN",
            "datetime64": "TIMESTAMP",
            "datetime64[ns]": "TIMESTAMP",
            "category": "TEXT",
        }
        col_defs: List[str] = []
        for col in columns:
            pg_type = type_map.get(col_types.get(col, "object"), "TEXT")
            safe_col = f'"{col}"' if not col.isidentifier() else col
            col_defs.append(f"  {safe_col} {pg_type}")

        body = ",\n".join(col_defs)
        return f"CREATE TABLE IF NOT EXISTS {table_name} (\n{body}\n);"
