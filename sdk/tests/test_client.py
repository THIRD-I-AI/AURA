"""HTTP client tests using respx to mock httpx."""
from __future__ import annotations

import httpx
import pytest
import respx

from aura_counterfactual import (
    AsyncClient,
    Client,
    EngineError,
    JobFailedError,
    JobTimeoutError,
    NotFoundError,
    RetryPolicy,
    ServiceUnavailableError,
)

BASE = "http://aura.test"


# ── info() ────────────────────────────────────────────────────────────

@respx.mock
def test_info_returns_engine_capabilities():
    respx.get(f"{BASE}/api/v1/counterfactual/info").mock(
        return_value=httpx.Response(200, json={
            "engine_version": "0.2.0",
            "dowhy_available": True,
            "signing_available": True,
            "signing_key_source": "env_hex",
            "pdf_available": True,
            "estimators": ["linear_regression", "ipw", "psm", "double_ml"],
            "refuters":   ["random_common_cause", "placebo", "data_subset", "sensitivity"],
            "audiences":  ["operator", "auditor", "analyst"],
        })
    )
    with Client(base_url=BASE) as c:
        info = c.info()
    assert info.engine_version == "0.2.0"
    assert info.dowhy_available is True
    assert info.signing_available is True
    assert info.estimators == ["linear_regression", "ipw", "psm", "double_ml"]


# ── replay() ──────────────────────────────────────────────────────────

@respx.mock
def test_replay_returns_typed_artifact(sample_artifact):
    record_hash = sample_artifact["audit_record_hash"]
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}").mock(
        return_value=httpx.Response(200, json=sample_artifact),
    )
    with Client(base_url=BASE) as c:
        art = c.replay(record_hash)
    assert art.audit_record_hash == record_hash
    assert art.confidence == "medium"


@respx.mock
def test_replay_404_raises_not_found_error():
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/" + "0" * 64).mock(
        return_value=httpx.Response(404, json={"detail": "not found"}),
    )
    with Client(base_url=BASE) as c:
        with pytest.raises(NotFoundError):
            c.replay("0" * 64)


# ── verify() ──────────────────────────────────────────────────────────

@respx.mock
def test_verify_returns_signed_status():
    record_hash = "a" * 64
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}/verify").mock(
        return_value=httpx.Response(200, json={
            "record_hash": record_hash,
            "verified": True,
            "signature_status": "signed",
            "signing_key_source": "env_hex",
            "reason": "ok",
        })
    )
    with Client(base_url=BASE) as c:
        result = c.verify(record_hash)
    assert result.verified
    assert result.signature_status == "signed"


@respx.mock
def test_public_key_pem_returned():
    respx.get(f"{BASE}/api/v1/counterfactual/public-key").mock(
        return_value=httpx.Response(200, json={
            "public_key_pem": "-----BEGIN PUBLIC KEY-----\nABC\n-----END PUBLIC KEY-----\n",
            "key_source": "env_hex",
        })
    )
    with Client(base_url=BASE) as c:
        pem = c.public_key_pem()
    assert "BEGIN PUBLIC KEY" in pem


# ── run() — submit + poll ─────────────────────────────────────────────

@respx.mock
def test_run_polls_until_succeeded(sample_artifact):
    submit_route = respx.post(f"{BASE}/api/v1/counterfactual/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "ca_xyz"}),
    )
    status_route = respx.get(f"{BASE}/api/v1/counterfactual/jobs/ca_xyz")
    status_route.mock(side_effect=[
        httpx.Response(200, json={"job_id": "ca_xyz", "state": "queued",
                                   "artifact": None, "error": None}),
        httpx.Response(200, json={"job_id": "ca_xyz", "state": "running",
                                   "artifact": None, "error": None}),
        httpx.Response(200, json={"job_id": "ca_xyz", "state": "succeeded",
                                   "artifact": sample_artifact, "error": None}),
    ])
    with Client(base_url=BASE) as c:
        art = c.run({"question": "x"}, poll_interval_s=0.001, timeout_s=5)
    assert submit_route.called
    assert status_route.called
    assert art.audit_record_hash == sample_artifact["audit_record_hash"]


@respx.mock
def test_run_raises_job_failed_when_engine_fails():
    respx.post(f"{BASE}/api/v1/counterfactual/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "ca_fail"}),
    )
    respx.get(f"{BASE}/api/v1/counterfactual/jobs/ca_fail").mock(
        return_value=httpx.Response(200, json={
            "job_id": "ca_fail", "state": "failed",
            "artifact": None, "error": "DoWhy crashed",
        }),
    )
    with Client(base_url=BASE) as c:
        with pytest.raises(JobFailedError, match="DoWhy crashed"):
            c.run({"question": "x"}, poll_interval_s=0.001, timeout_s=5)


@respx.mock
def test_run_raises_timeout_when_never_terminal():
    respx.post(f"{BASE}/api/v1/counterfactual/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "ca_slow"}),
    )
    respx.get(f"{BASE}/api/v1/counterfactual/jobs/ca_slow").mock(
        return_value=httpx.Response(200, json={
            "job_id": "ca_slow", "state": "running",
            "artifact": None, "error": None,
        }),
    )
    with Client(base_url=BASE) as c:
        with pytest.raises(JobTimeoutError):
            c.run({"question": "x"}, poll_interval_s=0.05, timeout_s=0.15)


# ── report_pdf() ──────────────────────────────────────────────────────

@respx.mock
def test_report_pdf_returns_bytes():
    record_hash = "a" * 64
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}/report.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 hello world",
                                     headers={"content-type": "application/pdf"}),
    )
    with Client(base_url=BASE) as c:
        data = c.report_pdf(record_hash)
    assert data.startswith(b"%PDF-")


@respx.mock
def test_report_pdf_501_raises_service_unavailable_immediately():
    """501 = feature deterministically unavailable in this deployment.
    Must NOT be retried (unlike a transient 503)."""
    record_hash = "a" * 64
    route = respx.get(
        f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}/report.pdf"
    ).mock(return_value=httpx.Response(501, text="reportlab missing"))
    with Client(base_url=BASE,
                retry=RetryPolicy(max_attempts=3, initial_delay_s=0.001)) as c:
        with pytest.raises(ServiceUnavailableError):
            c.report_pdf(record_hash)
    # 501 is non-retryable — single call, not three
    assert route.call_count == 1


# ── Retries ───────────────────────────────────────────────────────────

@respx.mock
def test_get_retries_on_transient_503_then_succeeds():
    record_hash = "a" * 64
    route = respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}/verify")
    route.mock(side_effect=[
        httpx.Response(503, text="overloaded"),
        httpx.Response(200, json={
            "record_hash": record_hash, "verified": True,
            "signature_status": "signed", "signing_key_source": "env_hex",
            "reason": "ok",
        }),
    ])
    with Client(base_url=BASE,
                retry=RetryPolicy(max_attempts=3, initial_delay_s=0.001)) as c:
        result = c.verify(record_hash)
    assert result.verified
    assert route.call_count == 2


@respx.mock
def test_get_gives_up_after_max_attempts():
    record_hash = "a" * 64
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}/verify").mock(
        return_value=httpx.Response(503, text="still down"),
    )
    with Client(base_url=BASE,
                retry=RetryPolicy(max_attempts=2, initial_delay_s=0.001)) as c:
        with pytest.raises(EngineError):
            c.verify(record_hash)


# ── API key threading ────────────────────────────────────────────────

@respx.mock
def test_api_key_sent_as_bearer_header():
    captured = {}

    def cap(request: httpx.Request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "engine_version": "0.2.0", "dowhy_available": True,
            "estimators": [], "refuters": [], "audiences": [],
        })

    respx.get(f"{BASE}/api/v1/counterfactual/info").mock(side_effect=cap)
    with Client(base_url=BASE, api_key="secret-token") as c:
        c.info()
    assert captured["auth"] == "Bearer secret-token"


# ── Standalone-service prefix ─────────────────────────────────────────

@respx.mock
def test_empty_prefix_routes_to_standalone_service():
    """When ``prefix=''``, the SDK hits /counterfactual/* directly
    (the standalone service surface)."""
    respx.get(f"{BASE}/counterfactual/info").mock(
        return_value=httpx.Response(200, json={
            "engine_version": "0.2.0", "dowhy_available": True,
            "estimators": [], "refuters": [], "audiences": [],
        }),
    )
    with Client(base_url=BASE, prefix="") as c:
        info = c.info()
    assert info.engine_version == "0.2.0"


# ── Async client smoke ────────────────────────────────────────────────

@respx.mock
@pytest.mark.asyncio
async def test_async_client_replay(sample_artifact):
    record_hash = sample_artifact["audit_record_hash"]
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}").mock(
        return_value=httpx.Response(200, json=sample_artifact),
    )
    async with AsyncClient(base_url=BASE) as c:
        art = await c.replay(record_hash)
    assert art.audit_record_hash == record_hash
