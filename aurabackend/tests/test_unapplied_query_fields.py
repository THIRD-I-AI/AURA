"""BUG-420: a query must not be signed as if a filter, limit, window or aggregation was applied."""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

import counterfactual_service.main as cf_main
from counterfactual_service import pdf_renderer
from counterfactual_service.schemas import CounterfactualQuery
from tests._synthetic_data import synthetic_dag_full

_USER = {"sub": "u1", "org_id": "acme"}


def _query(**dataset) -> CounterfactualQuery:
    return CounterfactualQuery(**{
        "question": "q",
        "treatment": {"column": "treatment", "actual": 1.0, "counterfactual": 0.0},
        "outcome": {"column": "outcome", "agg": "sum", "window": ["2025-07-01", "2025-09-30"]},
        "dag": {"edges": synthetic_dag_full()["edges"]},
        "dataset": {"source_id": "uploaded_file:sales.csv", **dataset},
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset", [{"where": "region = 'EU'"}, {"limit": 100}])
async def test_a_filter_or_row_limit_is_refused_at_submit(monkeypatch, dataset):
    async def _no_run(job_id, query):
        raise AssertionError("a refused query must not start a job")

    monkeypatch.setattr(cf_main, "_run_async", _no_run)
    with pytest.raises(HTTPException) as refused:
        await cf_main.submit_job(_query(**dataset), user=_USER)
    assert refused.value.status_code == 422
    assert "not supported" in refused.value.detail


@pytest.mark.asyncio
async def test_a_query_without_them_is_still_accepted(monkeypatch):
    async def _no_run(job_id, query):
        return None

    monkeypatch.setattr(cf_main, "_run_async", _no_run)
    assert "job_id" in await cf_main.submit_job(_query(), user=_USER)


def test_a_stored_query_that_carries_a_filter_still_parses():
    # Artifacts embed their query and are re-validated on replay.
    assert _query(where="region = 'EU'").dataset.where == "region = 'EU'"


def test_the_report_no_longer_presents_the_window_and_aggregation_as_applied():
    source = inspect.getsource(pdf_renderer)
    assert '"Aggregation",' not in source and '"Window",' not in source
    assert "average effect per row" in source and "all rows of the source" in source
