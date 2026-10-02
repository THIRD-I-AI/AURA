"""BUG-281: a misconfigured step was skipped -- or, for a filter operator, rewritten to
'=' -- and the run still reported SUCCESS on rows the step never touched."""
from __future__ import annotations

import pytest

from pipeline.engine import PipelineEngine
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


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend

    reset_storage_backend()
    yield
    reset_storage_backend()


async def _run(step_type: StepType, config: dict):
    from shared.storage import get_storage_backend

    get_storage_backend().write("acme", "t.csv", b"id,region,amount\n1,east,10\n2,west,20\n3,east,30\n")
    pipeline = Pipeline(
        name="p",
        source=PipelineSource(type=SourceType.FILE, file_name="t.csv"),
        steps=[ProcessingStep(type=step_type, config=config)],
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    return await PipelineEngine().execute(pipeline, preview_only=True, tenant="acme")


@pytest.mark.asyncio
@pytest.mark.parametrize("step_type,config,names", [
    (StepType.FILTER, {"column": "region", "operator": "BETWEEN", "value": "east"}, "BETWEEN"),
    (StepType.FILTER, {"operator": "=", "value": "east"}, "column"),
    (StepType.SORT, {"column": "amount", "direction": "SIDEWAYS"}, "SIDEWAYS"),
    (StepType.CAST_TYPE, {"column": "amount", "new_type": "DECIMAL"}, "DECIMAL"),
    (StepType.AGGREGATE, {"group_by": ["region"], "aggregations": [
        {"function": "SUM", "column": "amount"}, {"function": "COUNT_DISTINCT", "column": "id"}]}, "COUNT_DISTINCT"),
    (StepType.JOIN, {"left_key": "id", "right_key": "id", "right_table": "other"}, "join_source"),
    (StepType.WINDOW, {"function": "MEDIAN_ALL", "order_by": "id"}, "MEDIAN_ALL"),
    (StepType.DROP_COLUMNS, {"columns": []}, "columns"),
    (StepType.CUSTOM_SQL, {"expression": "  "}, "expression"),
])
async def test_a_misconfigured_step_fails_the_run_and_says_why(step_type, config, names):
    run = await _run(step_type, config)

    assert run.status == PipelineStatus.FAILED
    assert "misconfigured" in run.error and names in run.error


@pytest.mark.asyncio
async def test_not_equal_operator_is_not_executed_as_equals():
    run = await _run(StepType.FILTER, {"column": "region", "operator": "<>", "value": "east"})

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert [r["region"] for r in run.preview_data] == ["west"]


@pytest.mark.asyncio
async def test_fill_all_with_nothing_to_fill_is_still_a_no_op():
    run = await _run(StepType.FILL_MISSING, {"column": "*", "fill_value": "0"})

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert len(run.preview_data) == 3
