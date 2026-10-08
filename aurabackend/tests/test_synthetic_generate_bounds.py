"""BUG-227: /synthetic/generate writes to the server's disk, so its size and chunk
parameters must be bounded; /synthetic/plan (arithmetic only) stays unbounded."""
import pytest
from fastapi.testclient import TestClient

V1 = "/api/v1"
SCHEMA = {"name": "t", "columns": [{"name": "id", "type": "int"}]}


@pytest.fixture()
def client(monkeypatch):
    from api_gateway.main import app
    from api_gateway.routers import synthetic

    started = []
    monkeypatch.setattr(synthetic, "_run_job", lambda job_id, req: started.append(job_id))
    c = TestClient(app)
    c.started = started
    return c


def _gen(client, **over):
    body = {"schema": SCHEMA, "target_size": "10MB", "output_uri": "file:///out", **over}
    return client.post(f"{V1}/synthetic/generate", json=body)


def test_a_petabyte_generate_is_refused_before_any_job_starts(client):
    r = _gen(client, target_size="2PiB")
    assert r.status_code == 400 and "exceeds" in r.json()["detail"], r.text
    assert client.started == []


def test_a_small_generate_is_still_accepted(client):
    r = _gen(client)
    assert r.status_code == 200, r.text


def test_the_cap_is_configurable(client, monkeypatch):
    monkeypatch.setenv("AURA_SYNTHETIC_MAX_BYTES", str(5 * 10**6))
    assert _gen(client).status_code == 400
    monkeypatch.setenv("AURA_SYNTHETIC_MAX_BYTES", str(50 * 10**6))
    assert _gen(client).status_code == 200


@pytest.mark.parametrize("field,value", [
    ("chunk_rows", 0), ("chunk_rows", 10**9),
    ("max_files", 0), ("max_files", 10**9),
    ("file_target_bytes", 10), ("file_target_bytes", 10**12),
])
def test_out_of_range_parameters_are_rejected(client, field, value):
    r = _gen(client, **{field: value})
    assert r.status_code == 422, (field, value, r.status_code)
    assert client.started == []


def test_plan_stays_unbounded_for_sizing(client):
    r = client.post(f"{V1}/synthetic/plan", json={"schema": SCHEMA, "target_size": "2PiB"})
    assert r.status_code == 200, r.text


def test_generation_runs_on_its_own_pool_not_the_shared_default_executor(monkeypatch):
    """BUG-338: jobs ran on the event loop's default executor, which every
    asyncio.to_thread call shares; a few long jobs starved them all."""
    import threading
    import time

    from api_gateway.main import app
    from api_gateway.routers import synthetic

    ran_on = []
    monkeypatch.setattr(synthetic, "_run_job",
                        lambda job_id, req: ran_on.append(threading.current_thread().name))

    r = _gen(TestClient(app))

    assert r.status_code == 200, r.text
    deadline = time.time() + 5
    while not ran_on and time.time() < deadline:
        time.sleep(0.05)
    assert ran_on and ran_on[0].startswith("synthetic-gen"), ran_on


def test_a_wide_schema_is_generated_in_chunks_that_fit_in_memory(tmp_path, monkeypatch):
    # BUG-370: chunk_rows (<= 1M) alone did not bound memory: a 100-column float schema
    # built a 1M x 800-byte chunk (~800 MB) before writing anything.
    from synthetic import writer as w
    from synthetic.schema import TableSchema

    schema = TableSchema.from_dict({"name": "wide", "columns": [
        {"name": f"c{i}", "dtype": "float"} for i in range(100)]})
    writer = w.SyntheticDatasetWriter(schema, chunk_rows=1_000_000)

    assert writer.chunk_rows * w._in_memory_bytes_per_row(schema) <= 64 << 20
    assert writer.chunk_rows >= 1


@pytest.mark.parametrize("over", [
    {"schema": {"name": "t", "columns": [{"name": f"c{i}", "dtype": "float"} for i in range(300)]}},
    {"schema": {"name": "t", "columns": [{"name": "s", "dtype": "string", "prefix": "x" * 100_000}]}},
])
def test_unbounded_row_widths_are_refused(client, over):
    assert _gen(client, **over).status_code == 422
    assert client.started == []
