"""
Pipeline Agent
==============
Handles: Building and scheduling ETL/ELT pipelines by wiring together the
existing AURA scheduler microservice.  Creates pipeline definitions with
steps, dependencies, and cron schedules.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, BaseAgent, Severity
from shared.llm_provider import get_llm

_PIPELINE_PROMPT = """\
You are a data pipeline architect.  Given the user's request, generate a
pipeline definition as a JSON object.

USER REQUEST:
{request}

AVAILABLE SCHEMA:
{schema}

UPSTREAM RESULTS (previous agent outputs):
{upstream}

Return ONLY valid JSON with this structure (no markdown):
{{
  "name": "pipeline_name",
  "description": "what the pipeline does",
  "schedule": "cron expression or null for one-off",
  "steps": [
    {{
      "id": "step_1",
      "action": "ingest | transform | quality_check | export",
      "description": "what this step does",
      "sql": "SQL to run (if applicable)",
      "depends_on": []
    }}
  ]
}}
"""


class PipelineAgent(BaseAgent):
    name = "PipelineAgent"
    description = "Builds and schedules ETL/ELT pipelines."

    def __init__(self, tool_registry: Any = None) -> None:
        super().__init__(tool_registry)
        self._llm = get_llm(model=os.getenv("PIPELINE_MODEL", ""))

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Designing pipeline…", 10)

        pipeline_def = await self._design_pipeline(ctx)

        if not pipeline_def:
            result.add_step(
                action="pipeline_design_failed",
                output_summary="Could not generate pipeline definition",
                severity=Severity.WARNING,
            )
            result.output = {"pipeline": None}
            return result

        await self._report(f"Pipeline '{pipeline_def.get('name', '?')}' designed with "
                           f"{len(pipeline_def.get('steps', []))} step(s)", 50)

        result.add_step(
            action="pipeline_designed",
            output_summary=json.dumps(pipeline_def, indent=2)[:500],
        )

        # Register with scheduler if tool is available
        scheduled = False
        if self.tools and pipeline_def.get("schedule"):
            try:
                sched_result = await self.tools.call(
                    "create_schedule",
                    pipeline_id=pipeline_def.get("name", str(uuid.uuid4())),
                    cron=pipeline_def["schedule"],
                    payload=pipeline_def,
                )
                scheduled = True
                result.add_step(
                    action="pipeline_scheduled",
                    output_summary=f"Scheduled: {pipeline_def['schedule']}",
                )
            except Exception as exc:
                result.add_step(
                    action="schedule_error",
                    output_summary=str(exc),
                    severity=Severity.WARNING,
                )

        result.output = {
            "pipeline": pipeline_def,
            "scheduled": scheduled,
            "step_count": len(pipeline_def.get("steps", [])),
        }
        result.artifacts["pipeline_definition"] = pipeline_def
        return result

    # ------------------------------------------------------------------
    # Pipeline design
    # ------------------------------------------------------------------
    async def _design_pipeline(self, ctx: AgentContext) -> Optional[Dict[str, Any]]:
        schema_text = json.dumps(ctx.schema_context, indent=2) if ctx.schema_context else "{}"
        upstream_text = json.dumps(
            {k: str(v)[:300] for k, v in (ctx.upstream_results or {}).items()},
            indent=2,
        )

        if self._llm.is_available():
            try:
                prompt = _PIPELINE_PROMPT.format(
                    request=ctx.task_description,
                    schema=schema_text,
                    upstream=upstream_text,
                )
                parsed = self._llm.generate_json(prompt)
                if isinstance(parsed, dict) and "steps" in parsed:
                    return parsed
            except Exception:
                pass

        return self._fallback_pipeline(ctx)

    @staticmethod
    def _fallback_pipeline(ctx: AgentContext) -> Dict[str, Any]:
        """Heuristic pipeline when LLM is unavailable."""
        desc = ctx.task_description.lower()
        steps: List[Dict[str, Any]] = []

        step_id = 0

        def _add(action: str, description: str, sql: str = "", deps: List[str] | None = None) -> str:
            nonlocal step_id
            step_id += 1
            sid = f"step_{step_id}"
            steps.append({
                "id": sid,
                "action": action,
                "description": description,
                "sql": sql,
                "depends_on": deps or [],
            })
            return sid

        s1 = _add("ingest", "Ingest source data")

        if "clean" in desc or "transform" in desc:
            s2 = _add("transform", "Clean and transform data", deps=[s1])
        else:
            s2 = s1

        if "quality" in desc or "check" in desc or "validate" in desc:
            s3 = _add("quality_check", "Run quality checks", deps=[s2])
        else:
            s3 = s2

        _add("export", "Export / materialize results", deps=[s3])

        schedule = None
        if "daily" in desc:
            schedule = "0 2 * * *"
        elif "hourly" in desc:
            schedule = "0 * * * *"
        elif "weekly" in desc:
            schedule = "0 2 * * 1"

        return {
            "name": f"pipeline_{uuid.uuid4().hex[:8]}",
            "description": ctx.task_description[:200],
            "schedule": schedule,
            "steps": steps,
        }
