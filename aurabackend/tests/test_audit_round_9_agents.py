"""Audit round 9 regressions for the agents area (BUG-507, BUG-508)."""
from __future__ import annotations

import threading

import pytest

from agents.base import AgentContext, AgentStatus
from agents.planner import PlannerAgent
from agents.specialists.dar_research_agent import DARResearchAgent
from agents.specialists.ingestion_agent import IngestionAgent
from agents.specialists.optimization_agent import OptimizationAgent
from agents.specialists.pipeline_agent import PipelineAgent
from agents.specialists.transform_agent import TransformAgent

# ── BUG-507 ───────────────────────────────────────────────────────────


class _RecordingTools:
    def __init__(self):
        self.paths = []

    async def call(self, name, **kwargs):
        if name == "ingest_and_profile":
            self.paths.append(kwargs["file_path"])
            return {"rows": 1, "columns": ["a"]}
        return []


OWN = "/data/uploads/tenant-b/sales.csv"
FOREIGN = "tenant-a/payroll.csv"


@pytest.mark.asyncio
async def test_bug_507_llm_planned_files_outside_confined_ctx_files_are_not_ingested():
    tools = _RecordingTools()
    ctx = AgentContext(
        user_prompt="ingest", task_description="ingest",
        files=[OWN], metadata={"files": [FOREIGN]},
    )
    result = await IngestionAgent(tool_registry=tools).execute(ctx)

    assert FOREIGN not in tools.paths
    assert all(p == OWN for p in tools.paths)
    assert result.status == AgentStatus.SUCCESS


@pytest.mark.asyncio
async def test_bug_507_llm_planned_files_with_empty_ctx_files_are_not_ingested():
    tools = _RecordingTools()
    ctx = AgentContext(
        user_prompt="ingest", task_description="ingest",
        files=[], metadata={"files": [FOREIGN]},
    )
    await IngestionAgent(tool_registry=tools).execute(ctx)

    assert tools.paths == []


@pytest.mark.asyncio
async def test_bug_507_planner_can_still_pick_a_subset_of_own_files():
    other = "/data/uploads/tenant-b/costs.csv"
    tools = _RecordingTools()
    ctx = AgentContext(
        user_prompt="ingest", task_description="ingest",
        files=[OWN, other], metadata={"files": ["sales.csv"]},
    )
    await IngestionAgent(tool_registry=tools).execute(ctx)

    assert tools.paths == [OWN]


# ── BUG-508 ───────────────────────────────────────────────────────────


class _ThreadRecordingLLM:
    """Records which thread is_available() ran on; returns False so fallbacks run."""

    def __init__(self):
        self.threads = []

    def is_available(self):
        self.threads.append(threading.get_ident())
        return False

    def generate_json(self, *a, **k):  # pragma: no cover - never reached
        raise AssertionError("unavailable LLM must not be called")


def _with_llm(agent):
    llm = _ThreadRecordingLLM()
    agent.llm = llm
    agent._llm = llm
    return agent, llm


def _ctx():
    return AgentContext(user_prompt="build a pipeline", task_description="build a pipeline")


async def _planner(agent):
    await agent._call_llm("sys", "user")


async def _transform(agent):
    await agent._generate_transforms("clean it", "{}")


async def _optimization(agent):
    await agent._generate_recommendations("speed it up", "{}")


async def _pipeline(agent):
    await agent._design_pipeline(_ctx())


async def _dar(agent):
    await agent._call_llm_json("prompt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory,invoke",
    [
        (PlannerAgent, _planner),
        (TransformAgent, _transform),
        (OptimizationAgent, _optimization),
        (PipelineAgent, _pipeline),
        (DARResearchAgent, _dar),
    ],
    ids=["planner", "transform", "optimization", "pipeline", "dar"],
)
async def test_bug_508_is_available_probe_runs_off_the_event_loop(factory, invoke):
    agent, llm = _with_llm(factory())
    loop_thread = threading.get_ident()

    await invoke(agent)

    assert llm.threads, "is_available() was not consulted"
    assert loop_thread not in llm.threads
