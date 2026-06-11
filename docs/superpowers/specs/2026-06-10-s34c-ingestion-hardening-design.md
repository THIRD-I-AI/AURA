# S34c — Ingestion Hardening (lifespan, Kafka-fails-boot, contract CI) — Design

**Sprint:** S34c (third sub-sprint of S34; issue #60, branch `feature/s34-finance-auditor-pivot`)
**Date:** 2026-06-10

## Problems

1. `ingestion_service/main.py` uses deprecated `@app.on_event` (warns on every
   test run; removal scheduled in FastAPI 1.0).
2. **Kafka-fails-boot:** the startup hook does `await kafka_producer.start()`
   unguarded — if the broker is down the whole ingestion gateway refuses to
   boot, and a `None` producer makes `publish_with_retry` crash with
   `AttributeError` *inside* its own DLQ fallback (the DLQ is Kafka too).
3. `tests_contract/test_erp_contracts.py` is run by **no CI lane** —
   violates the repo's Tier A/B gating rule. Since `aiokafka` is now in base
   `requirements.txt`, these are effectively Tier A.

## Design

### 1. Tolerant producer (`kafka_client.py`)

* `start()` catches broker-connection failure: logs an error, leaves
  `self.producer = None` — **boot always succeeds** (a headless ingestion
  gateway that 503s its publishes is recoverable; one that won't boot is not).
* New `KafkaUnavailableError(RuntimeError)`. `publish_with_retry` first runs
  `_ensure_started()` (one lazy re-start attempt per call); if the broker is
  still down it raises `KafkaUnavailableError` instead of dying in the DLQ
  path — the DLQ stays reserved for *publish* failures when the broker is up.

### 2. Lifespan (`main.py`)

`create_service` already accepts a `lifespan` async context manager — pass
one that calls the tolerant `start()` on entry and `stop()` on exit; delete
both `@app.on_event` hooks. `process_raw_batch_async` catches
`KafkaUnavailableError` and records `audit_event("ingestion_publish_failed",
{batch_id, system_origin, entry_count, error})` — the batch was already
202-accepted, so the WORM trail is the traceability contract for the loss.

### 3. CI

Add `tests_contract/` to the base Backend Tests pytest invocation
(both Python matrix versions pick it up automatically).

## Out of scope

Disk-backed DLQ for broker-down windows (needs an ops decision on replay),
Kafka consumer wiring into the auditor, ingestion auth.

## Testing (Tier A, monkeypatched `AIOKafkaProducer`)

start() swallows connection failure; publish on dead broker raises
`KafkaUnavailableError` after one lazy retry; `process_raw_batch_async`
audit-logs the failure without raising; `TestClient(app)` boots (lifespan
runs) with the broker down and `/health` responds.
