"""
Chat-facing Counterfactual Audit Engine router.

In v1 we mount the ``counterfactual_service`` endpoints in-process
under ``/api/v1/counterfactual/`` instead of doing an httpx-proxied HTTP
hop. The wire format matches the standalone service so when Sprint 9
splits the service into its own pod we just swap the in-process call
for an httpx client without touching front-end code.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from counterfactual_service.main import (
    get_job as _svc_get,
)
from counterfactual_service.main import (
    info as _svc_info,
)
from counterfactual_service.main import (
    submit_job as _svc_submit,
)
from counterfactual_service.schemas import CounterfactualQuery

router = APIRouter(prefix="/counterfactual", tags=["counterfactual"])


@router.post("/jobs")
async def submit(query: CounterfactualQuery) -> Dict[str, Any]:
    return await _svc_submit(query)


@router.get("/jobs/{job_id}")
async def status(job_id: str) -> Dict[str, Any]:
    return await _svc_get(job_id)


@router.get("/info")
async def info() -> Dict[str, Any]:
    return await _svc_info()
