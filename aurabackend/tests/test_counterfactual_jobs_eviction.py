"""BUG-302: the job registry was never evicted -- every job, each demo click included,
stayed in memory with its full artifact for the life of the process."""
from __future__ import annotations

import pytest

from counterfactual_service import main as m


@pytest.fixture(autouse=True)
def _small_registry(monkeypatch):
    monkeypatch.setattr(m, "_jobs", {})
    monkeypatch.setattr(m, "MAX_RETAINED_JOBS", 5)


def _finish(job_id: str, state: str = "succeeded") -> None:
    m._jobs[job_id].update(state=state, artifact={"big": "x" * 100})


def test_the_registry_stays_at_its_cap_and_drops_the_oldest_finished_jobs():
    ids = []
    for _ in range(12):
        job_id = m._new_job("demo", "acme")
        _finish(job_id)
        ids.append(job_id)

    assert len(m._jobs) == 5
    assert list(m._jobs) == ids[-5:]


def test_a_queued_or_running_job_is_never_evicted():
    running = m._new_job("audit", "acme")
    m._jobs[running]["state"] = "running"
    queued = m._new_job("audit", "acme")

    for _ in range(20):
        _finish(m._new_job("demo", "acme"), "failed")

    assert running in m._jobs and queued in m._jobs
    assert len(m._jobs) == 5


def test_unfinished_jobs_alone_can_exceed_the_cap_rather_than_lose_work():
    ids = [m._new_job("audit", "acme") for _ in range(8)]

    assert all(j in m._jobs for j in ids)
