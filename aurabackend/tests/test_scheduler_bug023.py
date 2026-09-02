"""
Regression tests for BUG-023 (docs/BUG_REGISTRY.md).

BUG-023a: `list_executions`'s `status: Optional[JobStatus] = None` parameter
shadowed the module-level `fastapi.status` import, so the endpoint's own
`except Exception` block crashed with AttributeError (`status.HTTP_500_...`
resolved to the local param, not the fastapi module) instead of returning a
clean 500. Fixed by renaming the parameter to `status_filter`.

BUG-023b: `_calculate_next_execution`'s monthly branch fed an unvalidated
`day` straight into `datetime.replace()`, crashing a successful job as
FAILED when the source month has fewer days (e.g. day=31 in a 30-day
month). Fixed with Pydantic bounds validation on `schedule_config` at
job-creation time, plus a defensive clamp to the month's last day in
`executor.py` itself.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_list_executions_returns_clean_500_not_attributeerror():
    """Before the fix, any exception inside list_executions raised
    AttributeError from `status.HTTP_500_INTERNAL_SERVER_ERROR` (status
    shadowed by the endpoint's own query parameter) instead of a structured
    500 response. Here the repository is never initialized (lifespan not
    driven), so the handler's own `except Exception` block is guaranteed to
    fire — that is exactly the path BUG-023a broke.
    """
    from scheduler_service.main import scheduler_app

    client = TestClient(scheduler_app, raise_server_exceptions=False)
    resp = client.get("/executions")
    assert resp.status_code == 500
    body = resp.json()
    assert "detail" in body


def test_list_executions_status_filter_query_param_accepted():
    """status_filter (renamed from the shadowing `status`) is still a usable
    query parameter name, and doesn't collide with fastapi.status."""
    from scheduler_service.main import scheduler_app

    client = TestClient(scheduler_app, raise_server_exceptions=False)
    resp = client.get("/executions", params={"status_filter": "success"})
    # Repository isn't initialized in this test, so this still 500s cleanly
    # (not a 422 from bad query parsing, and not an AttributeError crash).
    assert resp.status_code == 500


def test_create_job_rejects_out_of_range_schedule_config_day():
    """day=31 must fail validation at job-creation time rather than crashing
    a later successful execution as FAILED (BUG-023b)."""
    from scheduler_service.main import CreateJobRequest

    with pytest.raises(ValidationError):
        CreateJobRequest(
            name="monthly-report",
            connection_id="conn-1",
            query="SELECT 1",
            schedule_type="monthly",
            schedule_config={"day": 32, "hour": 0, "minute": 0},
        )


def test_create_job_rejects_out_of_range_hour_and_minute():
    from scheduler_service.main import CreateJobRequest

    with pytest.raises(ValidationError):
        CreateJobRequest(
            name="daily-report",
            connection_id="conn-1",
            query="SELECT 1",
            schedule_type="daily",
            schedule_config={"hour": 25, "minute": 0},
        )
    with pytest.raises(ValidationError):
        CreateJobRequest(
            name="daily-report",
            connection_id="conn-1",
            query="SELECT 1",
            schedule_type="daily",
            schedule_config={"hour": 0, "minute": 61},
        )


def test_create_job_accepts_valid_schedule_config():
    from scheduler_service.main import CreateJobRequest

    req = CreateJobRequest(
        name="monthly-report",
        connection_id="conn-1",
        query="SELECT 1",
        schedule_type="monthly",
        schedule_config={"day": 31, "hour": 9, "minute": 30},
    )
    assert req.schedule_config == {"day": 31, "hour": 9, "minute": 30}


def test_calculate_next_execution_clamps_day_31_in_short_month():
    """Defense-in-depth: even a valid (1-31) day that doesn't exist in the
    current month must not raise — clamp to the month's actual last day
    instead of letting datetime.replace() crash a successful run."""
    from scheduler_service.executor import JobExecutor
    from scheduler_service.models import ScheduledJob

    executor = JobExecutor.__new__(JobExecutor)  # bypass __init__ (no repo needed)
    job = ScheduledJob(
        id="job-1",
        name="monthly-report",
        connection_id="conn-1",
        query="SELECT 1",
        schedule_type="monthly",
        schedule_config={"day": 31, "hour": 0, "minute": 0},
        is_active=True,
    )
    next_run = executor._calculate_next_execution(job)
    assert next_run is not None
    _, days_in_month = calendar.monthrange(next_run.year, next_run.month)
    assert next_run.day <= days_in_month
