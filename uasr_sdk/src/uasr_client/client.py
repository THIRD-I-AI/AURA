"""
Sync + async HTTP clients for the UASR self-healing API.

    from uasr_client import UASRClient

    with UASRClient("http://localhost:8000") as uasr:
        uasr.register_baseline("orders", rows=history)
        result = uasr.ingest("orders", rows=new_batch)
        if result.drift_detected:
            print(result.severity, result.shim_deployed)

Both a blocking :class:`UASRClient` and an :class:`AsyncUASRClient` are
provided; they share request/response handling via a small mixin.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import httpx

from .models import (
    BaselineResult,
    DeploymentInfo,
    GateResult,
    IngestResult,
    MetricsSnapshot,
    SourceInfo,
)

DEFAULT_TIMEOUT = 30.0
_USER_AGENT = "uasr-client/0.1.0"


# ─────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────
class UASRError(Exception):
    """Base class for all client errors."""


class UASRConnectionError(UASRError):
    """The service could not be reached."""


class UASRAPIError(UASRError):
    """The service returned a non-2xx response."""

    def __init__(self, status_code: int, detail: Any, url: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.url = url
        super().__init__(f"HTTP {status_code} from {url}: {detail}")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _rows_to_payload(
    rows: Sequence[Dict[str, Any]],
    columns: Optional[Sequence[str]],
) -> Dict[str, Any]:
    """Normalise a list-of-dict batch into the service's columns/rows shape.

    If ``columns`` is not given, they are inferred from the first row.
    """
    rows = list(rows)
    if columns is None:
        columns = list(rows[0].keys()) if rows else []
    return {"columns": list(columns), "rows": rows}


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        detail = resp.json()
        detail = detail.get("detail", detail) if isinstance(detail, dict) else detail
    except Exception:
        detail = resp.text
    raise UASRAPIError(resp.status_code, detail, str(resp.url))


# ─────────────────────────────────────────────────────────────────────
# Sync client
# ─────────────────────────────────────────────────────────────────────
class UASRClient:
    """Blocking client for the UASR self-healing service."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        headers = {"User-Agent": _USER_AGENT}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    # -- lifecycle ----------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "UASRClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- internal -----------------------------------------------------
    def _request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = self._http.request(method, path, **kw)
        except httpx.ConnectError as e:
            raise UASRConnectionError(f"cannot reach UASR at {self._http.base_url}: {e}") from e
        _raise_for_status(resp)
        return resp.json() if resp.content else {}

    # -- API ----------------------------------------------------------
    def deployment(self) -> DeploymentInfo:
        return DeploymentInfo.model_validate(self._request("GET", "/uasr/deployment"))

    def register_baseline(
        self,
        source_id: str,
        rows: Sequence[Dict[str, Any]],
        *,
        columns: Optional[Sequence[str]] = None,
        schema_snapshot: Optional[Dict[str, Any]] = None,
    ) -> BaselineResult:
        body = {"source_id": source_id, **_rows_to_payload(rows, columns)}
        if schema_snapshot is not None:
            body["schema_snapshot"] = schema_snapshot
        return BaselineResult.model_validate(self._request("POST", "/uasr/baseline", json=body))

    def ingest(
        self,
        source_id: str,
        rows: Sequence[Dict[str, Any]],
        *,
        batch_id: str = "",
        columns: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IngestResult:
        body = {"source_id": source_id, "batch_id": batch_id, **_rows_to_payload(rows, columns)}
        if metadata:
            body["metadata"] = metadata
        return IngestResult.model_validate(self._request("POST", "/uasr/ingest", json=body))

    def gate_check(
        self,
        source_id: str,
        rows: Sequence[Dict[str, Any]],
        *,
        batch_id: str = "",
        columns: Optional[Sequence[str]] = None,
    ) -> GateResult:
        body = {"source_id": source_id, "batch_id": batch_id, **_rows_to_payload(rows, columns)}
        return GateResult.model_validate(self._request("POST", "/uasr/gate/check", json=body))

    def drift_status(self, source_id: Optional[str] = None, *, limit: int = 50) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if source_id:
            params["source_id"] = source_id
        return self._request("GET", "/uasr/drift/status", params=params)

    def metrics(self, *, window_seconds: Optional[float] = None) -> MetricsSnapshot:
        params = {"window_seconds": window_seconds} if window_seconds is not None else None
        return MetricsSnapshot.model_validate(self._request("GET", "/uasr/metrics", params=params))

    def alerts(self, *, hu_floor: float = 0.3, resolution_floor: float = 0.5) -> Any:
        return self._request(
            "GET", "/uasr/metrics/alerts",
            params={"hu_floor": hu_floor, "resolution_floor": resolution_floor},
        )

    def sources(self) -> List[SourceInfo]:
        data = self._request("GET", "/uasr/sources")
        items = data.get("sources", data) if isinstance(data, dict) else data
        return [SourceInfo.model_validate(s) for s in items]

    def pending_approvals(self, *, limit: int = 50) -> Any:
        return self._request("GET", "/uasr/recovery/pending", params={"limit": limit})

    def approve(self, recovery_id: str, *, approver: str, note: Optional[str] = None) -> Any:
        body = {"approver": approver, "note": note}
        return self._request("POST", f"/uasr/recovery/{recovery_id}/approve", json=body)

    def reject(self, recovery_id: str, *, approver: str, reason: str) -> Any:
        body = {"approver": approver, "reason": reason}
        return self._request("POST", f"/uasr/recovery/{recovery_id}/reject", json=body)

    def rollback(self, source_id: str) -> Any:
        return self._request("POST", "/uasr/rollback", json={"source_id": source_id})

    def mapek_status(self) -> Any:
        return self._request("GET", "/uasr/mapek/status")


# ─────────────────────────────────────────────────────────────────────
# Async client
# ─────────────────────────────────────────────────────────────────────
class AsyncUASRClient:
    """Async client for the UASR self-healing service (same surface)."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        headers = {"User-Agent": _USER_AGENT}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncUASRClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = await self._http.request(method, path, **kw)
        except httpx.ConnectError as e:
            raise UASRConnectionError(f"cannot reach UASR at {self._http.base_url}: {e}") from e
        _raise_for_status(resp)
        return resp.json() if resp.content else {}

    async def deployment(self) -> DeploymentInfo:
        return DeploymentInfo.model_validate(await self._request("GET", "/uasr/deployment"))

    async def register_baseline(
        self,
        source_id: str,
        rows: Sequence[Dict[str, Any]],
        *,
        columns: Optional[Sequence[str]] = None,
        schema_snapshot: Optional[Dict[str, Any]] = None,
    ) -> BaselineResult:
        body = {"source_id": source_id, **_rows_to_payload(rows, columns)}
        if schema_snapshot is not None:
            body["schema_snapshot"] = schema_snapshot
        return BaselineResult.model_validate(await self._request("POST", "/uasr/baseline", json=body))

    async def ingest(
        self,
        source_id: str,
        rows: Sequence[Dict[str, Any]],
        *,
        batch_id: str = "",
        columns: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IngestResult:
        body = {"source_id": source_id, "batch_id": batch_id, **_rows_to_payload(rows, columns)}
        if metadata:
            body["metadata"] = metadata
        return IngestResult.model_validate(await self._request("POST", "/uasr/ingest", json=body))

    async def gate_check(
        self,
        source_id: str,
        rows: Sequence[Dict[str, Any]],
        *,
        batch_id: str = "",
        columns: Optional[Sequence[str]] = None,
    ) -> GateResult:
        body = {"source_id": source_id, "batch_id": batch_id, **_rows_to_payload(rows, columns)}
        return GateResult.model_validate(await self._request("POST", "/uasr/gate/check", json=body))

    async def drift_status(self, source_id: Optional[str] = None, *, limit: int = 50) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if source_id:
            params["source_id"] = source_id
        return await self._request("GET", "/uasr/drift/status", params=params)

    async def metrics(self, *, window_seconds: Optional[float] = None) -> MetricsSnapshot:
        params = {"window_seconds": window_seconds} if window_seconds is not None else None
        return MetricsSnapshot.model_validate(await self._request("GET", "/uasr/metrics", params=params))

    async def sources(self) -> List[SourceInfo]:
        data = await self._request("GET", "/uasr/sources")
        items = data.get("sources", data) if isinstance(data, dict) else data
        return [SourceInfo.model_validate(s) for s in items]

    async def approve(self, recovery_id: str, *, approver: str, note: Optional[str] = None) -> Any:
        body = {"approver": approver, "note": note}
        return await self._request("POST", f"/uasr/recovery/{recovery_id}/approve", json=body)

    async def reject(self, recovery_id: str, *, approver: str, reason: str) -> Any:
        body = {"approver": approver, "reason": reason}
        return await self._request("POST", f"/uasr/recovery/{recovery_id}/reject", json=body)

    async def rollback(self, source_id: str) -> Any:
        return await self._request("POST", "/uasr/rollback", json={"source_id": source_id})
