# uasr-client

Python SDK + CLI for the **AURA UASR** self-healing layer for data pipelines.

UASR watches each data source, detects distributional / schema / semantic
drift, and — when it can — heals the batch and commits the fix behind a
canary gate with a full audit trail. This package is the client surface:
a typed Python client (sync + async) and a `uasr` command-line tool over
the service's REST API.

## Install

```bash
pip install -e uasr_sdk        # from the repo
```

## Python

```python
from uasr_client import UASRClient

history = [{"amount": float(x)} for x in range(1000)]
new_batch = [{"amount": float(x) * 100} for x in range(1000)]   # x100 unit bug

with UASRClient("http://localhost:8000") as uasr:
    uasr.register_baseline("orders", rows=history)

    result = uasr.ingest("orders", rows=new_batch, batch_id="2024-06-01")
    if result.drift_detected:
        print(f"{result.severity} {result.drift_type} drift")
        print(f"shim deployed: {result.shim_deployed}, post-KL: {result.post_kl}")
```

Async is symmetric:

```python
from uasr_client import AsyncUASRClient

async with AsyncUASRClient("http://localhost:8000") as uasr:
    await uasr.register_baseline("orders", rows=history)
    result = await uasr.ingest("orders", rows=new_batch)
```

## CLI

```bash
export UASR_URL=http://localhost:8000

uasr deployment                              # show backend config
uasr baseline orders --file history.csv      # register a baseline
uasr ingest   orders --file batch.csv --batch-id b42
uasr drift    --source orders                # recent drift events
uasr metrics                                 # pipeline-health (Hᵤ)
uasr sources                                 # all sources + shim state
uasr approve  <recovery_id> --approver alice --note "verified"
uasr reject   <recovery_id> --approver alice --reason "false positive"
uasr rollback orders                         # revert last shim
```

Batches load from **CSV** (header row → columns; numeric cells auto-cast)
or **JSON** (a list of objects, or `{"columns": [...], "rows": [...]}`).
Use `-` to read JSON from stdin.

## Configuration

| Option      | Env var        | Default                  |
|-------------|----------------|--------------------------|
| `--url`     | `UASR_URL`     | `http://localhost:8000`  |
| `--api-key` | `UASR_API_KEY` | *(none)*                 |
| `--timeout` | —              | `30.0` s                 |

## Errors

All failures raise a subclass of `UASRError`:

- `UASRConnectionError` — the service is unreachable.
- `UASRAPIError` — the service returned a non-2xx response
  (`.status_code`, `.detail`).

## Testing

```bash
cd uasr_sdk && pytest
```

Unit tests run against a mock ASGI transport (fast, no backend). The
integration test drives the **real** UASR FastAPI app in-process over an
ASGI transport — a genuine detect → gate → heal → persist round-trip with
no network server — and is skipped automatically if `aurabackend` is not
importable.
