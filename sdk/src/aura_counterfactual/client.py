"""
Sync + async HTTP clients for the Counterfactual Audit Engine.

Both clients wrap exactly the endpoints the engine exposes under
``/api/v1/counterfactual/`` (when going through the API gateway) or
the bare ``/counterfactual/`` paths (when hitting the standalone
service directly). Set ``base_url`` accordingly.

Design notes:

* **Poll-to-completion is built in.** ``run`` blocks until the job
  succeeds, fails, or times out — analysts shouldn't have to write
  their own polling loop.
* **Errors are typed.** Every client method raises an ``EngineError``
  subclass with the HTTP status, body, and a structured ``reason`` when
  the engine returns a 4xx/5xx. ``httpx.HTTPError`` propagates only on
  network-level failures.
* **Retries** target only transient HTTP errors (429, 503) on idempotent
  GETs. ``run`` and ``submit`` (POSTs that allocate jobs) never retry —
  duplicate submissions would create duplicate audit-chain entries.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, Iterator, List, Optional, Union

import httpx

from .models import (
    CounterfactualArtifact,
    CounterfactualQuery,
    EngineInfo,
    JobStatus,
    VerifyResult,
)

# ── Exceptions ────────────────────────────────────────────────────────

class EngineError(RuntimeError):
    """Base class for any error from the counterfactual engine."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class JobFailedError(EngineError):
    """The engine accepted the job but it ended in state ``failed``."""


class JobTimeoutError(EngineError):
    """``run`` hit ``timeout_s`` before the job reached a terminal state."""


class NotFoundError(EngineError):
    """404 — typically a record_hash that doesn't exist in this deployment."""


class ServiceUnavailableError(EngineError):
    """503 — typically the PDF endpoint when reportlab isn't installed."""


# ── Retry policy ──────────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    """Bounded retry for idempotent GETs only.

    The default is intentionally conservative: 3 attempts total over
    ~3 seconds. Enough to ride through a deployment rolling-restart
    blip; not enough to mask a real outage."""
    max_attempts: int = 3
    initial_delay_s: float = 0.5
    backoff_factor: float = 2.0
    retryable_statuses: tuple[int, ...] = (429, 502, 503, 504)


# ── Sync client ───────────────────────────────────────────────────────

class Client:
    """Synchronous Counterfactual Audit Engine client."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        prefix: str = "/api/v1",
        api_key: Optional[str] = None,
        timeout: Union[float, httpx.Timeout] = 30.0,
        retry: Optional[RetryPolicy] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        """Create a client.

        :param base_url: scheme+host, no trailing slash. e.g. http://localhost:8000
        :param prefix:   URL prefix the engine sits behind. Use ``"/api/v1"``
                         for the API gateway, ``""`` for the standalone service.
        :param api_key:  optional bearer token; sent as ``Authorization: Bearer <key>``.
        :param timeout:  per-request httpx timeout.
        :param retry:    retry policy for idempotent GETs. Pass None to disable.
        :param client:   inject an existing ``httpx.Client`` (useful for tests
                         that need to share a mock transport).
        """
        self._base_url = base_url.rstrip("/")
        self._prefix = prefix.rstrip("/") if prefix else ""
        self._retry = retry or RetryPolicy()
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._owns_client = client is None
        self._http = client or httpx.Client(timeout=timeout, headers=headers)
        if not self._owns_client:
            # Caller-injected client; merge our headers without clobbering theirs
            for k, v in headers.items():
                self._http.headers.setdefault(k, v)

    # ── Lifecycle ─────────────────────────────────────────────────────

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ── Internal request plumbing ────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{self._base_url}{self._prefix}{path}"

    def _get(self, path: str, *, raw: bool = False) -> Any:
        last_exc: Optional[Exception] = None
        delay = self._retry.initial_delay_s
        for attempt in range(self._retry.max_attempts):
            try:
                resp = self._http.get(self._url(path))
            except httpx.HTTPError as exc:
                last_exc = exc
            else:
                if resp.status_code in self._retry.retryable_statuses:
                    last_exc = EngineError(
                        f"transient {resp.status_code}",
                        status_code=resp.status_code,
                        body=resp.text,
                    )
                else:
                    self._raise_for_status(resp)
                    return resp if raw else resp.json()
            if attempt < self._retry.max_attempts - 1:
                time.sleep(delay)
                delay *= self._retry.backoff_factor
        # Out of attempts
        if isinstance(last_exc, EngineError):
            raise last_exc
        raise EngineError(f"request to {path} failed after retries: {last_exc}") from last_exc

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = self._http.post(self._url(path), json=payload)
        except httpx.HTTPError as exc:
            raise EngineError(f"network error: {exc}") from exc
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        status = resp.status_code
        body = resp.text
        if status == 404:
            raise NotFoundError(f"not found: {resp.url}", status_code=status, body=body)
        if status == 501:
            # 501 = feature deterministically unavailable in this deployment
            # (e.g. reportlab not installed, signing key missing). Non-
            # transient — the caller should NOT retry.
            raise ServiceUnavailableError(
                f"feature unavailable: {body[:200]}", status_code=status, body=body,
            )
        if status == 503:
            # 503 stays conventional ("service temporarily unavailable").
            # The retry layer above ``_raise_for_status`` already handles
            # retrying these; if we reach this branch the retries were
            # exhausted, so surface as ServiceUnavailableError too.
            raise ServiceUnavailableError(
                f"service unavailable: {body[:200]}", status_code=status, body=body,
            )
        raise EngineError(f"HTTP {status}: {body[:200]}", status_code=status, body=body)

    # ── Public API ───────────────────────────────────────────────────

    def info(self) -> EngineInfo:
        """Return engine capabilities (DoWhy, signing, PDF, available estimators)."""
        return EngineInfo(**self._get("/counterfactual/info"))

    def submit(self, query: Union[CounterfactualQuery, Dict[str, Any]]) -> str:
        """Submit a job and return the ``job_id`` immediately, without polling."""
        if isinstance(query, CounterfactualQuery):
            payload = query.model_dump(mode="json")
        else:
            payload = dict(query)
        body = self._post("/counterfactual/jobs", payload)
        return body["job_id"]

    def status(self, job_id: str) -> JobStatus:
        """Fetch the current state of a job (queued / running / succeeded / failed)."""
        return JobStatus(**self._get(f"/counterfactual/jobs/{job_id}"))

    def run(
        self,
        query: Union[CounterfactualQuery, Dict[str, Any]],
        *,
        timeout_s: float = 180.0,
        poll_interval_s: float = 1.0,
    ) -> CounterfactualArtifact:
        """Submit and block until the job reaches a terminal state.

        Raises ``JobTimeoutError`` if ``timeout_s`` elapses before the
        engine reports succeeded/failed; ``JobFailedError`` if the
        engine reports failed.
        """
        job_id = self.submit(query)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            s = self.status(job_id)
            if s.state == "succeeded":
                if s.artifact is None:
                    raise EngineError(f"job {job_id} succeeded but artifact is missing")
                return CounterfactualArtifact(**s.artifact)
            if s.state == "failed":
                raise JobFailedError(s.error or f"job {job_id} failed without an error message")
            time.sleep(poll_interval_s)
        raise JobTimeoutError(f"job {job_id} did not complete within {timeout_s}s")

    def replay(self, record_hash: str) -> CounterfactualArtifact:
        """Fetch a previously-sealed artifact by its ``audit_record_hash``."""
        return CounterfactualArtifact(**self._get(f"/counterfactual/artifacts/{record_hash}"))

    def verify(self, record_hash: str) -> VerifyResult:
        """Verify the persisted ED25519 signature against the persisted bytes."""
        return VerifyResult(**self._get(f"/counterfactual/artifacts/{record_hash}/verify"))

    def public_key_pem(self) -> str:
        """Return the engine's current ED25519 public key as PEM."""
        return self._get("/counterfactual/public-key")["public_key_pem"]

    def report_pdf(self, record_hash: str) -> bytes:
        """Download the auditor PDF for a previously-sealed artifact.

        Raises ``ServiceUnavailableError`` (503) if the engine deployment
        lacks reportlab.
        """
        resp = self._get(
            f"/counterfactual/artifacts/{record_hash}/report.pdf",
            raw=True,
        )
        return resp.content

    def bulk_replay(self, hashes: Iterable[str]) -> Iterator[Dict[str, Any]]:
        """Stream verify results for a batch of artifact hashes.

        Yields one dict per hash, in submission order, as the engine
        finishes verifying each one. Per-hash failure modes
        (``not_found``, ``unsigned``, ``verify_failed``, ``error``)
        come back in the ``status`` field; the iterator never raises
        for individual failures. Network or HTTP-level failures still
        raise ``EngineError``.

        The endpoint streams NDJSON and the client streams the parse,
        so the auditor's caller can start consuming results before the
        last hash has been verified and memory stays O(1) regardless
        of batch size.

        ::

            for row in c.bulk_replay(["abc...", "def..."]):
                print(row["record_hash"], row["status"])
        """
        hash_list = list(hashes)
        if not hash_list:
            return
        with self._http.stream(
            "POST",
            self._url("/counterfactual/replay/bulk"),
            json={"hashes": hash_list},
        ) as resp:
            if not resp.is_success:
                # httpx needs the streaming body read before .text is
                # available inside the context manager; otherwise the
                # error path raises StreamConsumed instead of our
                # structured EngineError.
                resp.read()
            self._raise_for_status(resp)
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)


# ── Async client (mirror of the sync surface) ─────────────────────────

class AsyncClient:
    """Asynchronous Counterfactual Audit Engine client. Same surface as ``Client``."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        prefix: str = "/api/v1",
        api_key: Optional[str] = None,
        timeout: Union[float, httpx.Timeout] = 30.0,
        retry: Optional[RetryPolicy] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._prefix = prefix.rstrip("/") if prefix else ""
        self._retry = retry or RetryPolicy()
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(timeout=timeout, headers=headers)
        if not self._owns_client:
            for k, v in headers.items():
                self._http.headers.setdefault(k, v)

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _url(self, path: str) -> str:
        return f"{self._base_url}{self._prefix}{path}"

    async def _get(self, path: str, *, raw: bool = False) -> Any:
        import asyncio
        last_exc: Optional[Exception] = None
        delay = self._retry.initial_delay_s
        for attempt in range(self._retry.max_attempts):
            try:
                resp = await self._http.get(self._url(path))
            except httpx.HTTPError as exc:
                last_exc = exc
            else:
                if resp.status_code in self._retry.retryable_statuses:
                    last_exc = EngineError(
                        f"transient {resp.status_code}",
                        status_code=resp.status_code, body=resp.text,
                    )
                else:
                    Client._raise_for_status(resp)
                    return resp if raw else resp.json()
            if attempt < self._retry.max_attempts - 1:
                await asyncio.sleep(delay)
                delay *= self._retry.backoff_factor
        if isinstance(last_exc, EngineError):
            raise last_exc
        raise EngineError(f"request to {path} failed after retries: {last_exc}") from last_exc

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = await self._http.post(self._url(path), json=payload)
        except httpx.HTTPError as exc:
            raise EngineError(f"network error: {exc}") from exc
        Client._raise_for_status(resp)
        return resp.json()

    async def info(self) -> EngineInfo:
        return EngineInfo(**await self._get("/counterfactual/info"))

    async def submit(self, query: Union[CounterfactualQuery, Dict[str, Any]]) -> str:
        if isinstance(query, CounterfactualQuery):
            payload = query.model_dump(mode="json")
        else:
            payload = dict(query)
        body = await self._post("/counterfactual/jobs", payload)
        return body["job_id"]

    async def status(self, job_id: str) -> JobStatus:
        return JobStatus(**await self._get(f"/counterfactual/jobs/{job_id}"))

    async def run(
        self,
        query: Union[CounterfactualQuery, Dict[str, Any]],
        *,
        timeout_s: float = 180.0,
        poll_interval_s: float = 1.0,
    ) -> CounterfactualArtifact:
        import asyncio
        job_id = await self.submit(query)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            s = await self.status(job_id)
            if s.state == "succeeded":
                if s.artifact is None:
                    raise EngineError(f"job {job_id} succeeded but artifact is missing")
                return CounterfactualArtifact(**s.artifact)
            if s.state == "failed":
                raise JobFailedError(s.error or f"job {job_id} failed without an error message")
            await asyncio.sleep(poll_interval_s)
        raise JobTimeoutError(f"job {job_id} did not complete within {timeout_s}s")

    async def replay(self, record_hash: str) -> CounterfactualArtifact:
        return CounterfactualArtifact(**await self._get(f"/counterfactual/artifacts/{record_hash}"))

    async def verify(self, record_hash: str) -> VerifyResult:
        return VerifyResult(**await self._get(f"/counterfactual/artifacts/{record_hash}/verify"))

    async def public_key_pem(self) -> str:
        return (await self._get("/counterfactual/public-key"))["public_key_pem"]

    async def report_pdf(self, record_hash: str) -> bytes:
        resp = await self._get(
            f"/counterfactual/artifacts/{record_hash}/report.pdf",
            raw=True,
        )
        return resp.content

    async def bulk_replay(
        self, hashes: Iterable[str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Async streaming variant of ``Client.bulk_replay``.

        Same NDJSON contract from the server side; the async generator
        lets a FastAPI / async-notebook caller process results as they
        land without blocking the event loop.
        """
        hash_list = list(hashes)
        if not hash_list:
            return
        async with self._http.stream(
            "POST",
            self._url("/counterfactual/replay/bulk"),
            json={"hashes": hash_list},
        ) as resp:
            if not resp.is_success:
                # Body needs to be drained before httpx will let us
                # read .text inside the stream context manager.
                await resp.aread()
            Client._raise_for_status(resp)
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
