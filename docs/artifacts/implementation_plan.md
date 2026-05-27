# AURA Operational Readiness Plan (S25–S30)

This plan translates our design decisions into a concrete sprint roadmap to address the 12 weaknesses identified in the project analysis. We are prioritizing operational readiness over new analytic features for this phase.

## User Review Required

Please review the proposed sprint roadmap below. Once approved, I will begin executing the sprints in order, updating `docs/SPRINTS.md` as we progress.

## Open Questions

None at this time. The previous interview clarified all major design vectors.

## Proposed Changes: Sprint Roadmap

We will tackle the weaknesses through a hybrid approach: grouping related ops/debt items into dedicated sprints.

### Sprint S25: Code Health Hygiene
**Goal:** Rip the band-aid off the lint debt and clean up frontend cruft.
- **Backend Lint:** Enable `ruff check --fix` in pre-commit hooks to automate whitespace and formatting fixes (addresses the 316-file tab debt).
- **Backend Lint:** Manually audit and fix `F823` (undefined locals) and `F841` (unused variables) bugs.
- **Frontend Cleanup:** Delete `App.tsx.bak` and the `_legacy/` directory.

### Sprint S26: Observability (OpenTelemetry)
**Goal:** Add distributed tracing across the 12 microservices.
- Add `opentelemetry-distro` and `opentelemetry-instrumentation-fastapi` to requirements.
- Instrument the `create_service()` factory in `aurabackend/shared/service_factory.py` to automatically trace all incoming requests.
- Add a Jaeger or Zipkin container to `docker-compose.yml` for local trace viewing.

### Sprint S27: Docker Restructuring
**Goal:** Move from a fat monolithic image to optimized per-tier images.
- Create multi-stage `Dockerfile` (or separate Dockerfiles) for 3 tiers:
  1. `aura-base`: FastAPI, SQLAlchemy (Gateway, Scheduler, etc.)
  2. `aura-causal`: Base + dowhy, econml (Counterfactual)
  3. `aura-streaming`: Base + aiokafka (UASR, Pipeline)
- Update `docker-compose.yml` and `docker-compose.prod.yml` to use the tiered images.

### Sprint S28: CI Test Coverage Expansion
**Goal:** Close the testing blind spots in CI without breaking the base 60% gate.
- **New CI Lanes:** Add two new parallel jobs to `.github/workflows/ci.yml`: `causal-test` and `streaming-test`.
- **Dependencies:** Install the heavy dependencies (`dowhy`, `aiokafka`) only in their respective jobs to keep the base `backend-test` job fast.
- **Coverage:** Enforce coverage thresholds on the previously omitted directories (`causal_service/`, `uasr/mapek_worker.py`, etc.) within these specific lanes.

### Sprint S29: Integration Testing
**Goal:** Validate cross-service contracts and end-to-end flows.
- **Contract Tests (Schemathesis):** Add a CI job that locally boots each microservice (using `uvicorn`), hits `/openapi.json`, and runs `schemathesis` to fuzz the API contract.
- **E2E Nightly Tests:** Add a nightly GitHub Action that starts the entire `docker-compose.yml` stack and runs a new `tests_e2e/` `pytest` suite using `httpx` to validate cross-service communication (e.g., Gateway → Orchestrator).

### Sprint S30: CI/CD Pipeline (Build & Push)
**Goal:** Automate Docker image delivery to GitHub Container Registry (GHCR).
- **Trigger:** Workflow triggers on push to `main` (builds `latest`) and on release tags like `v*` (builds versioned tags).
- **Build Matrix:** Use Docker Buildx to build the 3 tiered targets defined in S27 (`base-runtime`, `causal-runtime`, `streaming-runtime`).
- **Push:** Authenticate with `GITHUB_TOKEN` and push the tagged images to `ghcr.io`.

## Verification Plan

### Automated Tests
- CI will remain green throughout.
- New CI lanes will enforce coverage on causal and streaming components.
- Schemathesis and integration test lanes will validate inter-service communication.

### Manual Verification
- Verify OpenTelemetry traces appear in local Jaeger/Zipkin UI.
- Confirm Docker images are significantly smaller (per-tier) than the previous fat image.
- Verify GHCR receives pushed images on merge to main.
