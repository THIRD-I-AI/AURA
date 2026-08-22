"""End-to-end: the exact sequence the browser performs, through the GATEWAY.

This file exists because a real break shipped past every other test. Making the
job endpoints tenant-scoped was verified service-direct and green, but the
frontend called them with a bare fetch and 401'd — the whole audit flow was
dead in the UI while the backend suite stayed green.

The gap: nothing walked login -> token -> run -> poll as ONE flow against the
mounted gateway app. Service-direct tests can't see it, because the gateway
mounts the service in-process and calls its handlers directly, so the service's
own Depends() never resolve — auth has to be re-declared on the gateway route,
and only a gateway-level test proves it was.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

V1 = "/api/v1"


@pytest.fixture()
def gw():
    from api_gateway.main import app
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_demo_run_and_poll_requires_and_accepts_a_token(gw, monkeypatch):
    """The browser flow: authenticate, start a demo audit, poll it to done."""
    from counterfactual_service import main as m
    monkeypatch.setitem(
        m._demo_last_good, "fair_lending", {"audit_record_hash": "e2e-hash"})

    # Anonymous is refused on both legs — this is the regression guard.
    assert gw.post(f"{V1}/counterfactual/demo/fair_lending").status_code == 401
    assert gw.get(f"{V1}/counterfactual/jobs/ca_nonexistent").status_code == 401

    from shared.auth import create_access_token
    headers = _bearer(create_access_token({"sub": "e2e@example.test",
                                           "org_id": "org-e2e"}))

    started = gw.post(f"{V1}/counterfactual/demo/fair_lending", headers=headers)
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]

    polled = gw.get(f"{V1}/counterfactual/jobs/{job_id}", headers=headers)
    assert polled.status_code == 200, polled.text
    body = polled.json()
    assert body["state"] == "succeeded"
    assert body["artifact"]["audit_record_hash"] == "e2e-hash"


def test_another_org_cannot_poll_the_job(gw, monkeypatch):
    """Tenant isolation end-to-end, not just at the service function."""
    from counterfactual_service import main as m
    monkeypatch.setitem(
        m._demo_last_good, "fair_lending", {"audit_record_hash": "private"})

    from shared.auth import create_access_token
    owner = _bearer(create_access_token({"sub": "owner", "org_id": "org-a"}))
    other = _bearer(create_access_token({"sub": "mallory", "org_id": "org-b"}))

    job_id = gw.post(f"{V1}/counterfactual/demo/fair_lending",
                     headers=owner).json()["job_id"]

    intruder = gw.get(f"{V1}/counterfactual/jobs/{job_id}", headers=other)
    # 404 rather than 403: a 403 would confirm the id is real and turn this
    # endpoint into an enumeration oracle.
    assert intruder.status_code == 404
    assert "private" not in intruder.text
    assert gw.get(f"{V1}/counterfactual/jobs/{job_id}",
                  headers=owner).status_code == 200


def test_financial_audit_route_resolves_its_dependency(gw):
    """Regression guard for the in-process-mount Depends() trap: this route
    used to 500 through the gateway (AttributeError on a Depends sentinel
    reaching _ledger_tenant) while working service-direct. Any status except
    500 means the dependency resolved; the body is irrelevant here."""
    r = gw.post(f"{V1}/counterfactual/audit/financial",
                json={"uploaded_file": "nope.csv"})
    assert r.status_code != 500, r.text


def test_job_approve_and_cancel_do_not_fake_success(gw) -> None:
    """A HITL gate must never claim it approved something it did not.

    Both endpoints previously read a module-level `_jobs_store` that nothing in
    the backend ever wrote to, so the lookup always missed and both fell through
    to an unconditional {"success": true} -- for ANY job id, including one that
    was never real. On an audit platform that is indistinguishable, to the
    caller and in the logs, from a genuine human approval.

    404 is the honest answer while no store backs them. What this test really
    guards is the invariant: never 200-with-success for an unknown job.
    """
    for verb in ("approve", "cancel"):
        r = gw.post(f"{V1}/jobs/definitely-not-a-real-job-id/{verb}", json={})
        assert r.status_code != 200, (
            f"/jobs/.../{verb} returned 200 for a job that does not exist"
        )
        if r.status_code == 404:
            body = r.json()
            assert body.get("success") is not True
