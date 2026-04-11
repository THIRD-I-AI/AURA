from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException

from shared.config import settings
from shared.logging_config import get_logger
from shared.models import ExecutionJob, QueryResult

# Add parent directory to path
from shared.service_factory import create_service

logger = get_logger("aura.execution_sandbox")

execution_app = create_service(
    name="Execution Sandbox",
    service_tag="execution_sandbox",
)


DATABASE_SERVICE_URL = settings.database_service_url
SANDBOX_TIMEOUT = settings.execution_timeout


def _infer_chart(columns: List[str]) -> Dict[str, Any]:
	normalized = [col.lower() for col in columns]
	if any(key in normalized[0] for key in ("date", "time")) and len(columns) >= 2:
		return {"type": "line"}
	if len(columns) >= 2 and any(term in normalized[1] for term in ("sum", "revenue", "count", "total")):
		return {"type": "bar"}
	return {"type": "table"}


@execution_app.post("/execute_sql", response_model=QueryResult)
async def execute_sql(job: ExecutionJob) -> QueryResult:
	if not job.approved:
		raise HTTPException(status_code=403, detail="Job must be approved before execution")
	if not job.connection_id:
		raise HTTPException(status_code=400, detail="connection_id is required")
	if not job.sql.strip():
		raise HTTPException(status_code=400, detail="SQL query is required")

	payload: Dict[str, Any] = {
		"connection_id": job.connection_id,
		"query": job.sql,
		"limit": job.limit,
	}

	try:
		async with httpx.AsyncClient(timeout=SANDBOX_TIMEOUT) as client:
			response = await client.post(
				f"{DATABASE_SERVICE_URL}/connections/{job.connection_id}/query",
				json=payload,
			)
	except httpx.RequestError as exc:
		raise HTTPException(status_code=502, detail=f"Database service unavailable: {exc}") from exc

	if response.status_code != 200:
		try:
			payload = response.json()
		except ValueError:
			payload = {"detail": response.text}
		raise HTTPException(status_code=response.status_code, detail=payload.get("detail", payload))

	data = response.json()
	columns = data.get("columns", [])
	rows = data.get("rows", [])
	chart_spec = _infer_chart(columns) if columns else {"type": "table"}
	chart_spec["row_count"] = data.get("row_count", len(rows))

	return QueryResult(
		columns=columns,
		rows=rows,
		chart_spec=chart_spec,
	)
