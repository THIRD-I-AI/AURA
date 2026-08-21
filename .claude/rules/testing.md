---
description: Test requirements, the pre-push gate, and the optional-dependency tier pattern
paths:
  - "aurabackend/tests/**"
  - "aurabackend/tests_contract/**"
  - "**/test_*.py"
  - "frontend/src/**/*.test.ts"
  - "frontend/src/**/*.test.tsx"
  - "frontend/e2e/**"
---

# Testing Requirements

- New features ship with their tests in the same change. Write the code and its
  assertions together, not the code first.
- Assert edge cases explicitly: unauthenticated caller, null/absent input,
  timeout, and the tenant-isolation boundary where one exists.
- Run the tests before reporting success. A test you did not run is not evidence.

## Pre-push protocol

Before pushing ANY commit, run these locally — CI blocks on them:

```sh
# Backend
cd aurabackend
../.venv/Scripts/python.exe -m ruff check --fix . \
  --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823
../.venv/Scripts/python.exe -m pytest tests/<the_file_you_touched>.py --tb=short

# Frontend (only if you touched it)
cd frontend && npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run
```

CI's ruff is **stricter** than that ignore list — it selects `E,F,I,W`. A clean
local ruff is not proof CI will pass.

**Never run pytest concurrently with a push.** The pre-push gate uses the same
test databases; a parallel run corrupts both.

## Tier A + Tier B, for optional dependencies

- **Tier A** — pure Python, no optional deps. Runs on the base backend lane always.
- **Tier B** — needs an optional dep (Postgres, dowhy, faiss). Gate with
  `pytest.mark.skipif(not <dep>_available())` or an `AURA_*_TEST_DSN` env var,
  and **add a dedicated CI lane that installs the dep and runs the file**.
  Never silently skip and hope the gate catches it — a skipped test reports green.

## Order dependence

A test that passes in the suite but fails alone is a defect, not a quirk: it is
consuming state some earlier test left behind. Make it create its own fixture
data. Verify both orderings before calling it fixed.
