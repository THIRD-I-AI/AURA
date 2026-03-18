"""
DAG Executor
============
Takes an ExecutionPlan (DAG of TaskNodes) produced by PlannerAgent and runs the
specialist agents in topological order, streaming progress via a callback.

Key design choices:
- Agents that have no unsatisfied dependencies run **concurrently**.
- Upstream results are automatically injected into each agent's context.
- If one task fails and nothing depends on it, execution continues.
- If a critical-path task fails, all downstream tasks are SKIPPED.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, ProgressCallback
from agents.planner import ExecutionPlan, TaskNode, TaskType
from agents.tool_registry import ToolRegistry
from agents.memory import AgentMemory

# Specialist imports
from agents.specialists.ingestion_agent import IngestionAgent
from agents.specialists.schema_architect_agent import SchemaArchitectAgent
from agents.specialists.transform_agent import TransformAgent
from agents.specialists.quality_agent import QualityAgent
from agents.specialists.pipeline_agent import PipelineAgent
from agents.specialists.optimization_agent import OptimizationAgent
from agents.specialists.sql_generator_agent import SQLGeneratorAgent


# ── Agent registry ────────────────────────────────────────────────────
AGENT_MAP: Dict[str, Type[BaseAgent]] = {
    "IngestionAgent": IngestionAgent,
    "SchemaArchitectAgent": SchemaArchitectAgent,
    "TransformAgent": TransformAgent,
    "QualityAgent": QualityAgent,
    "PipelineAgent": PipelineAgent,
    "OptimizationAgent": OptimizationAgent,
    "SQLGeneratorAgent": SQLGeneratorAgent,
}


class ExecutionReport:
    """Aggregated report after the full DAG finishes."""

    def __init__(self) -> None:
        self.task_results: Dict[str, AgentResult] = {}
        self.skipped: List[str] = []
        self.duration_ms: float = 0.0
        self.success: bool = True
        self.summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "summary": self.summary,
            "duration_ms": round(self.duration_ms, 1),
            "tasks": {
                tid: {
                    "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                    "output": r.output,
                    "suggestions": r.suggestions,
                    "artifacts": list(r.artifacts.keys()),
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                }
                for tid, r in self.task_results.items()
            },
            "skipped": self.skipped,
        }


class DAGExecutor:
    """Runs an ExecutionPlan through the specialist agents."""

    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        memory: Optional[AgentMemory] = None,
        progress_cb: Optional[ProgressCallback] = None,
        max_concurrency: int = 4,
    ) -> None:
        self.tools = tool_registry
        self.memory = memory or AgentMemory()
        self.progress_cb = progress_cb
        self.max_concurrency = max_concurrency

        # Instantiate agents
        self._agents: Dict[str, BaseAgent] = {}
        for name, cls in AGENT_MAP.items():
            agent = cls(tool_registry=self.tools)
            if self.progress_cb:
                agent.set_progress_callback(self.progress_cb)
            self._agents[name] = agent

    async def execute(
        self,
        plan: ExecutionPlan,
        user_prompt: str,
        connection: Optional[Dict[str, Any]] = None,
        files: Optional[List[str]] = None,
        schema_context: Optional[Dict[str, Any]] = None,
    ) -> ExecutionReport:
        """
        Run the entire plan.  Returns an ExecutionReport.
        """
        t0 = time.perf_counter()
        report = ExecutionReport()

        # State tracking
        completed: Dict[str, AgentResult] = {}
        failed: set[str] = set()
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run_task(task: TaskNode) -> None:
            async with semaphore:
                # Skip if any dependency failed
                for dep_id in task.depends_on:
                    if dep_id in failed:
                        task.status = AgentStatus.SKIPPED
                        report.skipped.append(task.id)
                        await self._progress(f"⏭ Skipped {task.id} (dependency failed)", "skip", 0)
                        return

                agent = self._agents.get(task.agent_name)
                if agent is None:
                    task.status = AgentStatus.FAILED
                    failed.add(task.id)
                    report.task_results[task.id] = AgentResult(
                        status=AgentStatus.FAILED,
                        error=f"Unknown agent: {task.agent_name}",
                    )
                    return

                # Build context
                upstream_results = {
                    dep_id: completed[dep_id].output
                    for dep_id in task.depends_on
                    if dep_id in completed
                }
                # Also merge schema from upstream
                merged_schema = dict(schema_context or {})
                for dep_id in task.depends_on:
                    if dep_id in completed:
                        dep_artifacts = completed[dep_id].artifacts
                        if "schema" in dep_artifacts:
                            merged_schema.update(dep_artifacts["schema"])

                ctx = AgentContext(
                    user_prompt=user_prompt,
                    task_description=task.description,
                    session_id=plan.plan_id,
                    upstream_results=upstream_results,
                    schema_context=merged_schema,
                    connection=connection,
                    files=files or [],
                    metadata=task.parameters or {},
                )

                await self._progress(
                    f"▶ Running {task.agent_name}: {task.description[:60]}",
                    task.agent_name,
                    0,
                )

                result = await agent.execute(ctx)
                task.result = result
                task.status = result.status

                if result.status == AgentStatus.FAILED:
                    failed.add(task.id)
                    report.success = False
                else:
                    completed[task.id] = result
                    # Store facts in memory
                    self.memory.add(
                        role="agent",
                        content=f"{task.agent_name} finished: {str(result.output)[:200]}",
                        agent_name=task.agent_name,
                    )

                report.task_results[task.id] = result

        # ── Topological execution with concurrency ────────────────────
        remaining = {t.id: t for t in plan.tasks}
        while remaining:
            # Find ready tasks (all deps satisfied)
            ready = [
                t for t in remaining.values()
                if all(
                    dep_id in completed or dep_id in failed or dep_id in [s for s in report.skipped]
                    for dep_id in t.depends_on
                )
            ]
            if not ready:
                # Deadlock — mark all remaining as skipped
                for t in remaining.values():
                    report.skipped.append(t.id)
                report.success = False
                break

            # Run ready tasks concurrently
            tasks = [asyncio.create_task(_run_task(t)) for t in ready]
            await asyncio.gather(*tasks)

            for t in ready:
                remaining.pop(t.id, None)

        report.duration_ms = (time.perf_counter() - t0) * 1000

        # Build summary
        success_count = sum(
            1 for r in report.task_results.values()
            if r.status == AgentStatus.SUCCESS
        )
        total = len(plan.tasks)
        report.summary = (
            f"Executed {success_count}/{total} tasks successfully "
            f"in {report.duration_ms:.0f}ms. "
            f"Skipped: {len(report.skipped)}. "
            f"Failures: {len(failed)}."
        )

        await self._progress(f"✅ {report.summary}", "executor", 100)
        return report

    async def _progress(self, message: str, agent: str, pct: float) -> None:
        if self.progress_cb:
            try:
                await self.progress_cb(message, agent, pct)
            except Exception:
                pass
