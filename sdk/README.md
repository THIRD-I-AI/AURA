# aura-counterfactual

Python SDK for the **AURA Counterfactual Audit Engine** — submit
counterfactual jobs, replay sealed artifacts, verify ED25519 signatures,
and download auditor PDFs from a typed, async-aware client. Includes a
`aura-counterfactual` CLI and Jupyter rich-rendering for
`CounterfactualArtifact` objects.

The SDK has **no runtime dependency on the AURA backend** — it speaks
only the engine's public HTTP wire format. Models are vendored at the
contract boundary and validated against the engine's schema in CI.

## Installation

```bash
pip install aura-counterfactual                # from PyPI (when published)
pip install -e .                                # editable, from this repo
pip install "aura-counterfactual[notebook]"     # adds ipython + jupyter
pip install "aura-counterfactual[dev]"          # adds pytest + respx + ruff
```

Requires Python 3.11+.

## Quickstart

```python
from aura_counterfactual import Client

with Client(base_url="http://localhost:8000") as c:
    # Inspect what the deployment supports
    info = c.info()
    print(info.engine_version, info.dowhy_available, info.pdf_available)

    # Run a counterfactual end-to-end
    artifact = c.run({
        "question":  "What would Q3 revenue have been if we hadn't raised prices in May?",
        "treatment": {"column": "price_change_may", "actual": 0.08, "counterfactual": 0.0},
        "outcome":   {"column": "monthly_revenue", "agg": "sum",
                       "window": ["2025-07-01", "2025-09-30"]},
        "dag":       {"edges": [
            ["seasonality",       "monthly_revenue"],
            ["price_change_may",  "monthly_revenue"],
            ["seasonality",       "price_change_may"],
        ]},
        "dataset":   {"source_id": "uploaded_file:sales_2025.csv"},
        "audience":  "analyst",
    })

    print(f"Average effect: {artifact.average_point:+.2f}")
    print(f"Confidence:     {artifact.confidence}")
    for c in artifact.high_severity_challenges:
        print(f"  high-severity challenge: {c.text}")

    # Replay later — byte-identical to what was sealed
    again = c.replay(artifact.audit_record_hash)
    assert again.audit_record_hash == artifact.audit_record_hash

    # Verify the signature without needing the private key
    result = c.verify(artifact.audit_record_hash)
    assert result.verified, result.reason

    # Download the auditor PDF
    pdf = c.report_pdf(artifact.audit_record_hash)
    open("report.pdf", "wb").write(pdf)
```

In a Jupyter cell, `artifact` renders as a styled HTML card with a
confidence badge, an estimator table, a refutations table, and a
collapsible challenges block — visually consistent with the operator
card in the AURA frontend.

## Async client

Same surface, async-native:

```python
from aura_counterfactual import AsyncClient

async with AsyncClient(base_url="http://localhost:8000") as c:
    artifact = await c.run({...})
    pem = await c.public_key_pem()
```

## CLI

The `aura-counterfactual` command is installed via the
`[project.scripts]` entry point.

```bash
# Engine capabilities
aura-counterfactual info
aura-counterfactual --json info

# Submit a query (loaded from a JSON file) and wait for the artifact
aura-counterfactual run query.json --save artifact.json

# Replay
aura-counterfactual replay 0xabc... --json

# Verify the ED25519 signature
aura-counterfactual verify 0xabc...

# Print the engine's current public key
aura-counterfactual public-key

# Download the auditor PDF
aura-counterfactual report 0xabc... -o report.pdf
```

Override the engine URL with `AURA_BASE_URL` (or `--base-url`) and the
bearer token with `AURA_API_KEY` (or `--api-key`).

Exit codes (for shell-pipeline use):

| Code | Meaning |
|------|---------|
| 0    | Success |
| 1    | Generic engine error or invalid input file |
| 2    | Job ran to terminal `failed` state |
| 3    | Job timed out (didn't reach succeeded/failed in time) |
| 4    | 404 — record_hash not found |
| 5    | Verify returned `verified=false` |
| 6    | 501 — feature unavailable in this deployment (e.g. PDF, signing) |

## Errors

```python
from aura_counterfactual import (
    EngineError,            # base class
    JobFailedError,         # engine ran the job, it ended in state="failed"
    JobTimeoutError,        # deadline expired before terminal state
    NotFoundError,          # 404 — record_hash unknown
    ServiceUnavailableError # 501 (deterministic) or 503-after-retries
)
```

## Retry policy

By default the client retries idempotent GETs up to 3 times with
exponential backoff (0.5s → 1s → 2s) on `429` / `502` / `503` / `504`.
`POST /jobs` is never retried — duplicate submissions would create
duplicate audit-chain entries. Override:

```python
from aura_counterfactual import Client, RetryPolicy

c = Client(retry=RetryPolicy(max_attempts=5, initial_delay_s=0.25))
c = Client(retry=None)   # disable retries entirely
```

## Going through the standalone service

The default `prefix="/api/v1"` targets the AURA API gateway. To talk to
the standalone `counterfactual_service` (port 8012) directly:

```python
c = Client(base_url="http://counterfactual:8012", prefix="")
```

## Audience tiers

Same artifact, three serialised shapes:

* `operator`  — the chat-card payload (point estimate, CI, top challenges, audit hash).
* `auditor`   — operator + full estimator/refutation tables + every challenge + signature.
* `analyst`   — auditor + the raw artifact dict for programmatic drill-down.

The SDK defaults to `analyst` so `Client.run()` returns the richest
payload. Override with `query["audience"] = "operator"` if you want
the slim version.

## Scope and known limits

* **Replay is byte-stable** because the engine persists canonical-JSON
  bytes. Re-running the engine on the same logical input may return a
  different `audit_record_hash` (DoWhy's PSM/IPW have unpinned RNG —
  Sprint 11+ pins them via seed-from-request_hash).
* The SDK is **read-mostly**. Submit + poll + replay + verify is the
  full surface; there is no support for editing or deleting artifacts.
  By design — TRAIGA artifacts are append-only.
* No streaming progress yet — `Client.run` polls. SSE-based progress
  is on the roadmap once the engine emits a per-step event stream.

## License

Proprietary — © THIRD-I-AI.
