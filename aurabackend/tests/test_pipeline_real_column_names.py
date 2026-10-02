"""BUG-286: a column a step referred to was passed through _sanitize_id, so a column
whose name has a space or punctuation could not be used in any step -- and when both
"unit-price" and "unit_price" existed, the step silently used the wrong one."""
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

CSV = (
    b'Order Date,unit-price,unit_price,Region (EU)\n'
    b'2026-01-02,5,500,north\n'
    b'2026-01-01,9,100,south\n'
    b'2026-01-03,7,300,north\n'
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

    get_storage_backend().write("acme", "orders.csv", CSV)
    pipeline = Pipeline(
        name="p",
        source=PipelineSource(type=SourceType.FILE, file_name="orders.csv"),
        steps=[ProcessingStep(type=step_type, config=config)],
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    run = await PipelineEngine().execute(pipeline, preview_only=True, tenant="acme")
    assert run.status == PipelineStatus.SUCCESS, run.error
    return run.preview_data


@pytest.mark.asyncio
async def test_sort_on_a_column_with_a_space():
    rows = await _run(StepType.SORT, {"column": "Order Date"})
    assert [str(r["Order Date"])[:10] for r in rows] == ["2026-01-01", "2026-01-02", "2026-01-03"]


@pytest.mark.asyncio
async def test_filter_uses_the_named_column_not_its_sanitized_twin():
    rows = await _run(StepType.FILTER, {"column": "unit-price", "operator": ">", "value": "6"})
    assert sorted(r["unit-price"] for r in rows) == [7, 9]


@pytest.mark.asyncio
async def test_aggregate_groups_and_sums_punctuated_columns():
    rows = await _run(StepType.AGGREGATE, {
        "group_by": ["Region (EU)"],
        "aggregations": [{"function": "SUM", "column": "unit-price", "alias": "total"}],
    })
    assert {r["Region (EU)"]: r["total"] for r in rows} == {"north": 12, "south": 9}


@pytest.mark.asyncio
async def test_drop_and_rename_address_the_real_column():
    rows = await _run(StepType.DROP_COLUMNS, {"columns": ["unit-price"]})
    assert "unit-price" not in rows[0] and "unit_price" in rows[0]

    rows = await _run(StepType.RENAME_COLUMNS, {"mapping": {"Order Date": "order_date"}})
    assert "order_date" in rows[0] and "Order Date" not in rows[0]


@pytest.mark.asyncio
async def test_a_quote_in_a_column_reference_cannot_break_out_of_the_identifier():
    from shared.storage import get_storage_backend

    get_storage_backend().write("acme", "orders.csv", CSV)
    pipeline = Pipeline(
        name="p",
        source=PipelineSource(type=SourceType.FILE, file_name="orders.csv"),
        steps=[ProcessingStep(type=StepType.SORT, config={"column": 'unit_price" DESC, (SELECT 1) --'})],
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    run = await PipelineEngine().execute(pipeline, preview_only=True, tenant="acme")

    assert run.status == PipelineStatus.FAILED
    assert "not found" in run.error.lower() or "binder" in run.error.lower()
