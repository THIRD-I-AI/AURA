"""BUG-436: a skipped task blocks its dependants, all the way down the chain."""
from __future__ import annotations

import pytest

from agents.base import AgentResult, AgentStatus
from agents.executor import DAGExecutor
from agents.planner import ExecutionPlan, TaskNode, TaskType


class _Recorder:
    def __init__(self):
        self.ran = []

    async def execute(self, ctx):
        self.ran.append(ctx.task_description)
        return AgentResult(status=AgentStatus.SUCCESS, output={})


@pytest.mark.asyncio
async def test_tasks_downstream_of_a_skipped_task_do_not_run(monkeypatch):
    # execute() ends by firing a background task that writes to the evolution
    # database. Left running, it outlives this test's event loop with the global
    # engine bound to it, and the next test that starts the gateway hangs in its
    # database start-up. This test is about scheduling only, so the write is stubbed.
    async def _no_record(plan, user_prompt, report):
        return None

    monkeypatch.setattr(DAGExecutor, "_record_in_evolution_engine", staticmethod(_no_record))
    executor = DAGExecutor()
    recorder = _Recorder()
    executor._agents = {"Stub": recorder}
    plan = ExecutionPlan(plan_id="p", user_prompt="q", tasks=[
        TaskNode(id="t1", task_type=TaskType.GENERATE_SQL, description="t1", agent_name="NoSuchAgent"),
        TaskNode(id="t2", task_type=TaskType.TRANSFORM, description="t2", agent_name="Stub", depends_on=["t1"]),
        TaskNode(id="t3", task_type=TaskType.TRANSFORM, description="t3", agent_name="Stub", depends_on=["t2"]),
        TaskNode(id="t4", task_type=TaskType.TRANSFORM, description="t4", agent_name="Stub", depends_on=["t3"]),
    ])

    report = await executor.execute(plan, "q")

    assert recorder.ran == []
    assert sorted(report.skipped) == ["t2", "t3", "t4"]
