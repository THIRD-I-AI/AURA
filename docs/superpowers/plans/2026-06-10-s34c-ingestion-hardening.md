# S34c — Ingestion Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingestion gateway boots with Kafka down, publishes fail loudly + auditable instead of crashing in the DLQ path, `on_event` → lifespan, `tests_contract/` gets a CI lane.

**Architecture:** tolerant `ResilientKafkaProducer.start()` + `KafkaUnavailableError`; `create_service(lifespan=...)`; base Backend Tests lane runs `tests_contract/`.

Spec: `docs/superpowers/specs/2026-06-10-s34c-ingestion-hardening-design.md`

### Task 1: tolerant producer

**Files:** Modify `aurabackend/ingestion_service/kafka_client.py`. Test: `aurabackend/tests/test_ingestion_resilience.py` (new).

- [ ] **Step 1:** Failing tests (monkeypatched `AIOKafkaProducer` whose `start()` raises): `test_start_swallows_broker_down` (no raise, producer stays None), `test_publish_raises_kafka_unavailable_after_lazy_retry` (counts start attempts == 2: boot + lazy), `test_publish_routes_dlq_only_when_broker_up`.
- [ ] **Step 2:** Implement `start()` try/except → log + `self.producer = None`; `_ensure_started()`; `KafkaUnavailableError`; guard at top of `publish_with_retry`.
- [ ] **Step 3:** Run the test file; commit `feat(s34c): Kafka-down no longer kills boot — tolerant start + KafkaUnavailableError`.

### Task 2: lifespan + auditable publish failure

**Files:** Modify `aurabackend/ingestion_service/main.py`. Test: extend `test_ingestion_resilience.py`.

- [ ] **Step 1:** Failing tests: `test_process_raw_batch_audits_publish_failure` (audit_event captured, no raise), `test_app_boots_with_broker_down` (`TestClient(app)` → GET `/health` 200, no `on_event` deprecation warning).
- [ ] **Step 2:** Implement `@asynccontextmanager` lifespan passed to `create_service`; delete both `on_event` hooks; wrap publish in `try/except KafkaUnavailableError` → `audit_event("ingestion_publish_failed", ...)`.
- [ ] **Step 3:** Run file + `tests_contract/test_erp_contracts.py`; commit `feat(s34c): ingestion lifespan + auditable publish failure`.

### Task 3: CI lane for tests_contract/

**Files:** Modify `.github/workflows/ci.yml` (base Backend Tests run step: `tests/ tests_contract/`).

- [ ] **Step 1:** Edit the pytest invocation; run `tests_contract/` locally to confirm green.
- [ ] **Step 2:** CI-exact ruff; commit `ci(s34c): run tests_contract/ in the base backend lane`; push; watch CI.
