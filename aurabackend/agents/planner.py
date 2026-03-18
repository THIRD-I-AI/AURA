"""
Planner Agent
==============
Accepts a single user prompt and decomposes it into an execution plan —
a DAG of TaskNodes that specialist agents execute in order.

This is the "brain" of the agentic DE system.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# Add parent dir for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import (
    AgentContext,
    AgentResult,
    AgentStatus,
    BaseAgent,
    Severity,
)
from agents.memory import AgentMemory

try:
    import google.generativeai as genai
except ImportError:
    genai = None  # type: ignore


# ────────────────────────────────────────────────────────────────────
# Plan data structures
# ────────────────────────────────────────────────────────────────────

class TaskType(str, Enum):
    INGEST = "ingest"
    SCHEMA_DESIGN = "schema_design"
    TRANSFORM = "transform"
    QUALITY_CHECK = "quality_check"
    PIPELINE_BUILD = "pipeline_build"
    OPTIMIZE = "optimize"
    EXECUTE_SQL = "execute_sql"
    GENERATE_SQL = "generate_sql"
    MONITOR = "monitor"


@dataclass
class TaskNode:
    """One node in the execution DAG."""
    id: str
    task_type: TaskType
    description: str
    agent_name: str                             # which specialist handles this
    depends_on: List[str] = field(default_factory=list)  # IDs of prerequisite tasks
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: AgentStatus = AgentStatus.PENDING
    result: Optional[AgentResult] = None

    @property
    def is_ready(self) -> bool:
        """Can run when all dependencies succeeded."""
        return self.status == AgentStatus.PENDING


@dataclass
class ExecutionPlan:
    """A full plan: ordered list of tasks with dependency edges."""
    plan_id: str
    user_prompt: str
    tasks: List[TaskNode] = field(default_factory=list)
    summary: str = ""
    estimated_duration_sec: int = 0

    def ready_tasks(self, completed_ids: set[str]) -> List[TaskNode]:
        """Return tasks whose dependencies are all satisfied."""
        ready = []
        for t in self.tasks:
            if t.status != AgentStatus.PENDING:
                continue
            if all(dep in completed_ids for dep in t.depends_on):
                ready.append(t)
        return ready

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "user_prompt": self.user_prompt,
            "summary": self.summary,
            "estimated_duration_sec": self.estimated_duration_sec,
            "tasks": [
                {
                    "id": t.id,
                    "task_type": t.task_type.value,
                    "description": t.description,
                    "agent_name": t.agent_name,
                    "depends_on": t.depends_on,
                    "parameters": t.parameters,
                    "status": t.status.value,
                }
                for t in self.tasks
            ],
        }


# ────────────────────────────────────────────────────────────────────
# Planner Agent
# ────────────────────────────────────────────────────────────────────

_PLANNER_SYSTEM_PROMPT = """\
You are AURA's Planner Agent — an expert data engineering architect.

Given a user's request, decompose it into an ordered list of tasks that
specialist agents will execute.  Each task must specify:
  - id: a short unique string (e.g. "t1", "t2")
  - task_type: one of {task_types}
  - description: what to do, in plain English
  - agent_name: which agent handles it (one of {agent_names})
  - depends_on: list of task IDs that must complete first ([] for root tasks)
  - parameters: any extra config the agent needs (JSON object)

Also provide:
  - summary: a one-line summary of the overall plan
  - estimated_duration_sec: rough estimate in seconds

AGENT ROSTER:
{agent_roster}

RULES:
1. Start with ingestion / schema discovery if the user references files or databases.
2. Always add a quality_check task after transforms.
3. If the user asks for a "pipeline", emit pipeline_build + monitor tasks.
4. Keep the plan minimal — don't add tasks the user didn't ask for.
5. Return ONLY valid JSON matching the schema below. No markdown fences.

OUTPUT SCHEMA:
{{
  "summary": "...",
  "estimated_duration_sec": 60,
  "tasks": [
    {{
      "id": "t1",
      "task_type": "ingest",
      "description": "...",
      "agent_name": "IngestionAgent",
      "depends_on": [],
      "parameters": {{}}
    }}
  ]
}}
"""

# Agent roster — the planner tells the LLM what's available
AGENT_ROSTER = {
    "IngestionAgent": "Ingests files (CSV, Excel, JSON, Parquet) or connects to databases. Profiles data.",
    "SchemaArchitectAgent": "Designs / inspects / modifies table schemas. Creates tables, adds indexes.",
    "TransformAgent": "Applies SQL-based transformations: joins, aggregations, window functions, pivots, type casts.",
    "QualityAgent": "Runs data quality checks: null rates, uniqueness, range validation, anomaly detection.",
    "PipelineAgent": "Builds scheduled ETL/ELT pipelines with the scheduler. Chains ingestion → transform → load.",
    "OptimizationAgent": "Analyzes query performance, recommends indexes, partitioning, and materialized views.",
    "SQLGeneratorAgent": "Generates ad-hoc SQL from natural language (existing Gemini code-gen).",
}


class PlannerAgent(BaseAgent):
    """
    Takes a user prompt → produces an ExecutionPlan (DAG of TaskNodes).
    Uses Gemini to reason about the decomposition.
    """

    name = "PlannerAgent"
    description = "Decomposes a user prompt into an execution plan of DE tasks."

    def __init__(self, tool_registry: Any = None, memory: Optional[AgentMemory] = None) -> None:
        super().__init__(tool_registry)
        self.memory = memory or AgentMemory()
        self._model = self._init_model()

    @staticmethod
    def _init_model() -> Any:
        if genai is None:
            return None
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None
        try:
            configure_fn = getattr(genai, "configure", None)
            if callable(configure_fn):
                configure_fn(api_key=api_key)
            model_cls = getattr(genai, "GenerativeModel", None)
            if model_cls:
                return model_cls(os.getenv("PLANNER_MODEL", "gemini-2.5-flash"))
        except Exception:
            pass
        return None

    # ── core logic ──────────────────────────────────────────────────

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Analyzing your request…", 10)

        # Build the system prompt
        task_types = ", ".join(t.value for t in TaskType)
        agent_names = ", ".join(AGENT_ROSTER.keys())
        agent_roster_text = "\n".join(
            f"  • {name}: {desc}" for name, desc in AGENT_ROSTER.items()
        )
        system_prompt = _PLANNER_SYSTEM_PROMPT.format(
            task_types=task_types,
            agent_names=agent_names,
            agent_roster=agent_roster_text,
        )

        # Context from memory
        memory_text = self.memory.as_text(last_n=20)
        schema_context = json.dumps(ctx.schema_context) if ctx.schema_context else "No schema loaded yet."

        user_message = (
            f"USER REQUEST: {ctx.user_prompt}\n\n"
            f"AVAILABLE SCHEMA:\n{schema_context}\n\n"
            f"FILES: {', '.join(ctx.files) if ctx.files else 'None'}\n\n"
            f"PREVIOUS CONTEXT:\n{memory_text or 'First interaction.'}"
        )

        await self._report("Creating execution plan…", 30)

        plan_json = await self._call_llm(system_prompt, user_message)

        if plan_json is None:
            # Fallback: single SQL generation task
            plan_json = self._fallback_plan(ctx)

        await self._report("Validating plan…", 70)

        plan = self._parse_plan(ctx, plan_json)

        result.output = plan.to_dict()
        result.artifacts["plan"] = plan
        result.add_step(
            action="plan_created",
            output_summary=f"{len(plan.tasks)} tasks: {plan.summary}",
        )

        self.memory.add(
            "agent", f"Created plan with {len(plan.tasks)} tasks: {plan.summary}",
            agent_name=self.name,
        )

        await self._report(f"Plan ready — {len(plan.tasks)} tasks", 100)
        return result

    # ── LLM call ────────────────────────────────────────────────────

    async def _call_llm(self, system_prompt: str, user_message: str) -> Optional[Dict[str, Any]]:
        if self._model is None:
            return None
        try:
            response = self._model.generate_content([system_prompt, user_message])
            text = (response.text or "").strip()
            # strip markdown fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()
            return json.loads(text)
        except Exception:
            return None

    # ── fallback (no LLM) ──────────────────────────────────────────

    @staticmethod
    def _fallback_plan(ctx: AgentContext) -> Dict[str, Any]:
        """Heuristic plan when Gemini is unavailable."""
        tasks: List[Dict[str, Any]] = []
        prompt_lower = ctx.user_prompt.lower()
        tid = 0

        def _next_id() -> str:
            nonlocal tid
            tid += 1
            return f"t{tid}"

        # If files mentioned → ingest
        if ctx.files or any(w in prompt_lower for w in ["upload", "ingest", "load", "csv", "excel", "parquet", "file"]):
            t_id = _next_id()
            tasks.append({
                "id": t_id,
                "task_type": "ingest",
                "description": "Ingest uploaded files and profile the data",
                "agent_name": "IngestionAgent",
                "depends_on": [],
                "parameters": {"files": ctx.files},
            })
            prev_id = t_id
        else:
            prev_id = None

        # Schema work
        if any(w in prompt_lower for w in ["schema", "table", "create", "design", "column", "index"]):
            t_id = _next_id()
            tasks.append({
                "id": t_id,
                "task_type": "schema_design",
                "description": "Design or inspect the database schema",
                "agent_name": "SchemaArchitectAgent",
                "depends_on": [prev_id] if prev_id else [],
                "parameters": {},
            })
            prev_id = t_id

        # Transform
        if any(w in prompt_lower for w in ["transform", "clean", "join", "merge", "aggregate", "pivot", "etl", "elt"]):
            t_id = _next_id()
            tasks.append({
                "id": t_id,
                "task_type": "transform",
                "description": "Apply data transformations",
                "agent_name": "TransformAgent",
                "depends_on": [prev_id] if prev_id else [],
                "parameters": {},
            })
            prev_id = t_id

            # Auto-add quality check after transform
            t_id = _next_id()
            tasks.append({
                "id": t_id,
                "task_type": "quality_check",
                "description": "Validate data quality after transformations",
                "agent_name": "QualityAgent",
                "depends_on": [prev_id],
                "parameters": {},
            })
            prev_id = t_id

        # Pipeline
        if any(w in prompt_lower for w in ["pipeline", "schedule", "automate", "recurring", "cron"]):
            t_id = _next_id()
            tasks.append({
                "id": t_id,
                "task_type": "pipeline_build",
                "description": "Build a scheduled data pipeline",
                "agent_name": "PipelineAgent",
                "depends_on": [prev_id] if prev_id else [],
                "parameters": {},
            })
            prev_id = t_id

        # Optimization
        if any(w in prompt_lower for w in ["optimize", "slow", "performance", "index", "partition"]):
            t_id = _next_id()
            tasks.append({
                "id": t_id,
                "task_type": "optimize",
                "description": "Optimize database performance",
                "agent_name": "OptimizationAgent",
                "depends_on": [prev_id] if prev_id else [],
                "parameters": {},
            })
            prev_id = t_id

        # Default: at least generate SQL
        if not tasks:
            tasks.append({
                "id": _next_id(),
                "task_type": "generate_sql",
                "description": "Generate SQL to answer the user's question",
                "agent_name": "SQLGeneratorAgent",
                "depends_on": [],
                "parameters": {},
            })

        return {
            "summary": f"Auto-generated plan with {len(tasks)} tasks",
            "estimated_duration_sec": len(tasks) * 15,
            "tasks": tasks,
        }

    # ── parse & validate ────────────────────────────────────────────

    @staticmethod
    def _parse_plan(ctx: AgentContext, raw: Dict[str, Any]) -> ExecutionPlan:
        tasks: List[TaskNode] = []
        for t in raw.get("tasks", []):
            try:
                task_type = TaskType(t.get("task_type", "execute_sql"))
            except ValueError:
                task_type = TaskType.EXECUTE_SQL

            tasks.append(TaskNode(
                id=t.get("id", f"t{len(tasks)+1}"),
                task_type=task_type,
                description=t.get("description", ""),
                agent_name=t.get("agent_name", "SQLGeneratorAgent"),
                depends_on=t.get("depends_on", []),
                parameters=t.get("parameters", {}),
            ))

        return ExecutionPlan(
            plan_id=ctx.run_id,
            user_prompt=ctx.user_prompt,
            tasks=tasks,
            summary=raw.get("summary", ""),
            estimated_duration_sec=raw.get("estimated_duration_sec", 0),
        )
