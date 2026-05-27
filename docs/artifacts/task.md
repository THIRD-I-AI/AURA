# Operational Readiness Tasks

## Sprint S25: Code Health Hygiene
- `[x]` Delete `frontend/src/App.tsx.bak`
- `[x]` Delete `frontend/src/_legacy/` directory
- `[x]` Configure `ruff` to auto-fix whitespace/formatting (remove suppresses for W191, W291, W293, E501 if appropriate, or just run it with `--fix`).
- `[x]` Run `ruff check --fix` to resolve trivial lint debt.
- `[x]` Audit and fix `F823` (undefined locals)
- `[x]` Audit and fix `F841` (unused variables)
- `[x]` Update `docs/SPRINTS.md` to mark S25 as in-flight/completed.

## Sprint S26: Observability (OpenTelemetry)
- `[x]` Add `opentelemetry-distro` and `opentelemetry-instrumentation-fastapi` to requirements.txt
- `[x]` Instrument `create_service()` in `shared/service_factory.py` to trace all requests
- `[x]` Add Jaeger container to `docker-compose.yml` for local trace viewing
- `[x]` Update `docs/SPRINTS.md` to reflect S26 completion

## Sprint S27: Docker Restructuring
- `[x]` Split monolithic `Dockerfile` into a multi-stage file with `base`, `causal`, and `streaming` targets
- `[x]` Create `requirements-streaming.txt` to separate `aiokafka` and streaming dependencies (if not already separate)
- `[x]` Update `docker-compose.yml` and `docker-compose.prod.yml` to build specific targets per service
- `[x]` Update `docs/SPRINTS.md` to reflect S27 completion

## Sprint S28: CI Test Coverage Expansion
- `[x]` Update `.github/workflows/ci.yml` to add `causal-test` job running `aurabackend/causal_service` and `aurabackend/counterfactual_service` tests with `dowhy` installed
- `[x]` Update `.github/workflows/ci.yml` to add `streaming-test` job running `aurabackend/uasr` and `aurabackend/pipeline` tests with `aiokafka` installed
- `[x]` Update `docs/SPRINTS.md` to reflect S28 completion

## Sprint S29: Integration Testing
- `[x]` Create `.github/workflows/nightly-e2e.yml` to run the `docker-compose` E2E test suite nightly
- `[x]` Create `aurabackend/tests_e2e/test_compose.py` with `httpx` tests against the local compose stack
- `[x]` Update `.github/workflows/ci.yml` to include a `contract-test` job running `schemathesis`
- `[x]` Add `schemathesis` to `requirements.txt` (or install it directly in CI)
- `[x]` Update `docs/SPRINTS.md` to reflect S29 completion

## Sprint S30: CI/CD Pipeline (Build & Push)
- `[x]` Create `.github/workflows/cd.yml` with Buildx matrix for `base`, `causal`, and `streaming` images
- `[x]` Configure Docker metadata to handle `latest` vs `v*` tags correctly
- `[x]` Update `docs/SPRINTS.md` to reflect S30 completion
