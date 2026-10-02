"""BUG-280: rows from a PostgreSQL/MySQL/Kafka source were loaded as all-VARCHAR, so a
filter, sort or MIN/MAX on a numeric column compared text and returned wrong rows with
a SUCCESS status.

The connector and the Kafka consumer are replaced by fakes that return the same shape
the real ones do (a list of dicts with native Python values); no local Postgres/Kafka
is available to the base test lane."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

import connectors
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

ROWS = [
    {"id": 1, "amount": 95, "price": Decimal("9.50"), "day": dt.date(2026, 1, 9), "ref": uuid.UUID(int=1), "meta": {"a": 1}, "note": None},
    {"id": 2, "amount": 1000, "price": Decimal("10.25"), "day": dt.date(2026, 1, 10), "ref": uuid.UUID(int=2), "meta": {"a": 2}, "note": None},
    {"id": 3, "amount": 200, "price": Decimal("100.00"), "day": dt.date(2025, 12, 31), "ref": uuid.UUID(int=3), "meta": [1, 2], "note": None},
]


class _FakeConnector:
    def __init__(self, config):
        pass

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def execute_query(self, query, limit=None, raise_errors=False):
        return [dict(r) for r in ROWS]


@pytest.fixture
def fake_postgres(monkeypatch):
    monkeypatch.setattr(connectors, "PostgreSQLConnector", _FakeConnector)


async def _run(steps, source=None):
    pipeline = Pipeline(
        name="p",
        source=source or PipelineSource(type=SourceType.POSTGRESQL, table="orders", connection={}),
        steps=steps,
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    run = await PipelineEngine().execute(pipeline, preview_only=True, tenant="acme")
    assert run.status == PipelineStatus.SUCCESS, run.error
    return run.preview_data


@pytest.mark.asyncio
async def test_numeric_filter_compares_numbers_not_text(fake_postgres):
    rows = await _run([ProcessingStep(
        type=StepType.FILTER, config={"column": "amount", "operator": ">", "value": "100"})])
    assert sorted(r["id"] for r in rows) == [2, 3]


@pytest.mark.asyncio
async def test_sort_orders_numbers_numerically(fake_postgres):
    rows = await _run([ProcessingStep(type=StepType.SORT, config={"column": "amount"})])
    assert [r["amount"] for r in rows] == [95, 200, 1000]


@pytest.mark.asyncio
async def test_columns_keep_their_source_types(fake_postgres):
    rows = await _run([])
    first = rows[0]
    assert first["amount"] == 95 and not isinstance(first["amount"], str)
    assert float(first["price"]) == 9.5 and not isinstance(first["price"], str)
    assert str(first["day"]).startswith("2026-01-09") and not isinstance(first["day"], str)


@pytest.mark.asyncio
async def test_values_arrow_cannot_type_are_loaded_as_text(fake_postgres):
    rows = await _run([])
    assert rows[0]["ref"] == str(uuid.UUID(int=1))
    assert rows[0]["meta"] == '{"a": 1}'
    assert rows[2]["meta"] == "[1, 2]"
    assert rows[0]["note"] is None


@pytest.mark.asyncio
async def test_kafka_rows_keep_numeric_types(monkeypatch):
    import shared.kafka_client as kafka_client

    async def _consume(cfg, progress_cb=None):
        return [{"id": 1, "amount": 95}, {"id": 2, "amount": 1000, "extra": {"k": "v"}}, {"id": 3, "amount": 200}]

    monkeypatch.setattr(kafka_client, "consume_batch", _consume)

    rows = await _run(
        [ProcessingStep(type=StepType.FILTER, config={"column": "amount", "operator": ">", "value": "100"})],
        source=PipelineSource(type=SourceType.KAFKA, connection={"topic": "t"}),
    )
    assert sorted(r["id"] for r in rows) == [2, 3]
