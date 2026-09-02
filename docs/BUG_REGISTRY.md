# AURA Bug Registry

Every anomaly `scripts/verify_live_deployment.py` (or any other live/manual
testing pass) finds gets an entry here **before** any fix is attempted.
This is the process, not a suggestion:

1. File the entry the moment an anomaly is observed — status `open`, root
   cause `unconfirmed` if not yet investigated. Don't skip straight to a fix.
2. Triage: either `false-positive` (state why — bad test data, an already-
   documented exclusion, etc. — and stop) or a confirmed root cause with
   file:line citations.
3. Confirmed bugs are ranked by severity and fixed **one at a time** — one
   bug, one branch, one PR. Never bundle unrelated fixes; a bundled fix
   breaks the lineage tracking below (unclear which change caused which
   regression).
4. Before marking `fixed`, re-run the specific check that failed (not the
   whole suite) to confirm the fix actually resolves it live.
5. If a later run finds a NEW anomaly, check whether it plausibly traces to
   a recent fix (same file/endpoint/timeframe) before filing it as
   unrelated. If it does, set **Caused by** to the prior bug's ID instead
   of starting a disconnected investigation — this field is the lineage
   graph: `grep "Caused by: BUG-012"` reconstructs which fixes spawned
   which regressions, without needing separate tooling.
6. Goal state: the verification suite is green and every entry here is
   `fixed` / `false-positive` / `wontfix` with a citation — no entry left
   `open`. That is what "solid base" means, made checkable.

## Severity

- `blocks-feature` — a promised capability doesn't work at all.
- `degrades-accuracy` — it works, but gives a wrong/incomplete result.
- `cosmetic` — works correctly, presentation/wording issue only.

## Entry template

```
## BUG-<NNN>: <short title>
- **Status:** open | false-positive | fixed | wontfix
- **Found by:** <verification run date/name, e.g. "live-verify 2026-08-31">
- **Severity:** blocks-feature | degrades-accuracy | cosmetic
- **Root cause:** <one paragraph, with file:line citations>
- **Caused by:** BUG-<NNN> | none
- **Fix:** <PR link, once opened>
```

---

## Pre-existing entries (found before this registry existed, backfilled for continuity)

## BUG-001: RecoveryLoop._validate_shim false-rejects a correct schema-fix shim
- **Status:** fixed
- **Found by:** live-verify 2026-08-31 (manual, staging: https://dataaura.duckdns.org)
- **Severity:** blocks-feature (UASR schema-drift auto-heal)
- **Root cause:** `aurabackend/uasr/recovery_loop.py:422` (pre-fix; the
  now-fixed check lives at line 437) — the schema-drift
  success check required `not post_drift.drift_detected` (zero drift of
  ANY kind), which was provably dead code: reaching that line already
  required `post_drift.drift_detected` to be True, so it could never
  diverge from the earlier check on line 339. A shim that correctly fixed
  SCHEMA drift was rejected whenever the corrected batch also carried
  unrelated statistical drift — a normal real-world co-occurrence.
- **Caused by:** none
- **Fix:** #264 (`fix/uasr-schema-validation-false-reject`)

## BUG-002: metrics.record_from_loop_result mis-attributes drift_type
- **Status:** fixed
- **Found by:** live-verify 2026-08-31 (manual, staging: https://dataaura.duckdns.org)
- **Severity:** degrades-accuracy (`GET /uasr/metrics` dashboards)
- **Root cause:** `aurabackend/uasr/metrics.py` — `record_from_loop_result`
  inferred `drift_type`/`severity` from `loop_result.diagnosis` via
  `hasattr(diag, "drift_type")`. `DiagnosisResult` (uasr/models.py) has no
  such field at all, so the check was always False and every recorded
  event silently defaulted to `STATISTICAL`/`LOW` regardless of the real
  drift — not just on failed recoveries, on every call.
- **Caused by:** none
- **Fix:** #264 (`fix/uasr-schema-validation-false-reject`, same PR as
  BUG-001 — bundled because both were found and fixed via the same live
  investigation before this one-bug-per-PR discipline was formalized;
  future entries follow the strict one-at-a-time rule above)

## BUG-003: Gateway proxies only 7 of ~20 UASR endpoints
- **Status:** fixed
- **Found by:** live-verify 2026-08-31 (manual, staging: https://dataaura.duckdns.org)
- **Severity:** blocks-feature (`/uasr/heal`, the actual self-healing
  endpoint, was unreachable from outside the box)
- **Root cause:** `aurabackend/api_gateway/routers/pipelines.py` only
  defined gateway routes for `ingest`/`baseline`/`metrics`/`drift/status`/
  `recovery/pending`/`approve`/`reject`. `/uasr/heal`, `/uasr/deployment`,
  `/uasr/correlation`, `/uasr/rollback`, `/uasr/recovery/{id}`, and others
  had no facade at all.
- **Caused by:** none
- **Fix:** #263 (`fix/uasr-gateway-proxy-gaps`), which also adds
  `tests/test_uasr_gateway_facade_coverage.py` so this class of gap fails
  CI going forward instead of silently 404ing in production.

## BUG-004: test_counterfactual_sprint13.py rate-limit flake in the full suite
- **Status:** fixed
- **Found by:** pre-push gate, feature/uasr-cross-source-correlation, 2026-08-31
- **Severity:** cosmetic (test infra, not product) — tracked here anyway
  because a flaky pre-push gate erodes trust in every other green run.
- **Root cause, confirmed with a citation:**
  `test_bulk_replay_returns_ndjson_with_mixed_statuses`
  (`aurabackend/tests/test_counterfactual_sprint13.py:104-115`) polled
  `GET /counterfactual/jobs/{id}` at a flat 0.5s interval for up to 300s —
  up to 600 requests — against the same global in-process rate limiter
  (`shared/config.py:423-424`, 100 req/60s per IP by default) every other
  request in the suite shares through one `TestClient`. Once the async
  job legitimately took more than ~50s under ordinary suite load (the
  test's own comment already measured ~47s unloaded), the poll loop's
  *own* request volume alone breached the 100/60s window and 429'd
  itself — a real, load-dependent self-inflicted limit, not the
  "order/volume-dependent, not yet root-caused" flakiness this entry
  originally guessed at. `conftest.py`'s `_reset_rate_limit_counters`
  fixture only resets once before the test starts; it cannot help once a
  single test's own steady-state request rate exceeds the bucket.
- **Caused by:** none
- **Fix:** backed the poll interval off from 0.5s to a 1.5x-per-iteration
  ramp capped at 2s, same commit as this entry. Caps steady-state polling
  at 30 req/60s (well under the 100/60s budget) while keeping the fast
  0.5s cadence for the common case where the job finishes quickly.
  Re-verified: passed in isolation (25s) and the fix does not touch
  product code, only the test's own polling cadence.

## BUG-009: test_counterfactual_sprint9.py has the identical rate-limit flake
- **Status:** fixed
- **Found by:** pre-push gate, feature/uasr-mapek-recovery-persistence,
  2026-09-01 — the real cause of a push that appeared to fail outright
  (not hang): `test_replay_endpoint_returns_artifact` failed with
  `KeyError: 'state'` after a 3h34m pre-push run (vs. the normal ~11min),
  itself inflated by unrelated zombie pytest processes from the BUG-008
  investigation contending for the same shared test DB during most of
  that run.
- **Severity:** cosmetic (test infra, not product) — same reasoning as
  BUG-004.
- **Root cause:** `test_replay_endpoint_returns_artifact`
  (`aurabackend/tests/test_counterfactual_sprint9.py:329-345`) has the
  exact same copy-pasted 300s/flat-0.5s poll loop BUG-004 fixed in
  `test_counterfactual_sprint13.py` — a sibling file that fix never
  touched. Confirmed by re-running the test in isolation post-fix-attempt:
  it failed there too (with a *different* `KeyError` each time —
  `'public_key_pem'` in the contaminated run, `'state'` in a clean
  isolated re-run), consistent with the same self-inflicted-rate-limit
  mechanism as BUG-004, not the concurrent-process contamination alone.
- **Caused by:** none — pre-existing, copy-pasted from the same origin
  as BUG-004's bug, just never caught in that fix's scope.
- **Fix:** identical backoff (0.5s ramping to 2s, same commit as this
  entry), plus an explicit `assert resp.status_code == 200` before
  indexing the JSON body, so a future rate-limit hit fails with a readable
  message instead of a bare `KeyError`. Re-verified: passed in isolation
  (65.8s).
- **Note:** `aurabackend/tests/test_synthetic_api.py:113-121` has a
  related but lower-risk pattern (same flat-0.5s poll, but already
  guarded by `assert jr.status_code == 200` and a shorter 120s budget) —
  not fixed here, flagged for the next person who touches that file.

## BUG-011: test_counterfactual_engine.py's _poll_until_done has the identical rate-limit flake
- **Status:** fixed
- **Found by:** repo-wide sweep for the BUG-004/BUG-009 poll-loop pattern,
  2026-09-01.
- **Severity:** cosmetic (test infra, not product) — same reasoning as
  BUG-004/BUG-009.
- **Root cause:** `_poll_until_done`
  (`aurabackend/tests/test_counterfactual_engine.py:293-317`), used by
  `test_service_endpoint_roundtrip` and `test_gateway_proxies_
  counterfactual`, is a third, independently-named copy of the same
  flat-0.5s/300s-budget poll loop BUG-004 and BUG-009 fixed in the
  sprint13 and sprint9 files — this file's helper was never in either
  fix's scope. A flat 0.5s interval is 2 req/s, i.e. up to 120 req/60s
  sustained — already above the shared rate limiter's 100 req/60s budget
  (`shared/config.py:423-424`) on its own once the DoWhy job's
  ~47s-unloaded fan-out runs long under suite load, independent of any
  other test's traffic. The helper also called `client.get(url).json()`
  directly with no `status_code` check.
- **Caused by:** none — pre-existing, copy-pasted from the same origin as
  BUG-004/BUG-009's bug, just in a file neither fix's scope touched.
- **Fix:** same backoff as BUG-009 (0.5s ramping to 2s), plus an explicit
  `assert resp.status_code == 200` before indexing the JSON body.
  Re-verified: `test_counterfactual_engine.py` full file, 10/10 passed
  (325.5s).
- **Note:** swept the rest of `aurabackend/tests/` for the same pattern
  and found no further unfixed instances — `test_synthetic_api.py`'s copy
  (already flagged as lower-risk by BUG-009's note) is the only other
  one, left as-is per that note.

## BUG-005: unauthenticated-by-design verification endpoints return 401 live
- **Status:** fixed
- **Found by:** live-verify 2026-08-31 (`scripts/verify_live_deployment.py`,
  first run against https://dataaura.duckdns.org — `jwks` check failed)
- **Severity:** blocks-feature — external cryptographic verification is a
  core promised capability (TRAIGA federation, S19): a third-party auditor
  is supposed to verify AURA's ED25519 signatures via `/jwks` and RFC 6962
  Merkle proofs via `/audit/sth` + `/audit/inclusion/{proof}` *without* an
  AURA login. On any deployment with auth correctly armed, none of them
  are reachable, which defeats the entire point of the feature.
- **Root cause:** `aurabackend/shared/middleware.py:116-119` —
  `_PUBLIC_PATHS` (the global `JWTAuthMiddleware`'s allowlist) never
  included `/api/v1/counterfactual/jwks`, `/api/v1/counterfactual/audit/sth`,
  or `/api/v1/counterfactual/audit/inclusion/{proof}` — despite each route
  handler being explicitly coded and commented as intentionally
  unauthenticated (`counterfactual.py`'s `jwks()`: *"Service route is
  unauthed (public key material), so this proxy takes no auth either"*).
  The existing regression tests (`test_jwks_reachable_unauthenticated`,
  `test_sth_reachable_unauthenticated`, `test_inclusion_proof_reachable_
  unauthenticated` in `tests/test_gateway_facade_coverage.py`) all pass
  today only because `AURA_JWT_ENABLED` defaults to `False` in the test
  environment, so `JWTAuthMiddleware` is never installed in that test run
  at all — they never exercise the "auth armed" configuration production
  actually runs under. Same class of gap as BUG-001/BUG-002: a check that
  passes in an environment shape production doesn't use.
- **Caused by:** none
- **Fix:** #266 (`fix/uasr-public-verification-endpoints`) — added
  `/api/v1/counterfactual/jwks` and `/api/v1/counterfactual/audit/sth` to
  `_PUBLIC_PATHS`, and a new `_PUBLIC_PATH_PREFIXES` + `_is_public_path()`
  helper for the parameterized `/api/v1/counterfactual/audit/inclusion/{proof_hash}`
  route, applied to both `APIKeyMiddleware` and `JWTAuthMiddleware`. Sibling
  route `/api/v1/counterfactual/audit/financial` deliberately left requiring
  auth (negative test added). Merging the PR alone did not fix it live —
  the box pulls images on a manual `docker compose pull && up -d`, not on
  merge; the fix sat unreleased on `main` until the box's `AURA_TAG` was
  bumped from the stale pinned `0.1.4` to `latest` (the tag CD actually
  publishes on a plain push to `main`) and the stack was redeployed,
  2026-08-31. Re-verified live post-redeploy with direct curl checks
  against https://dataaura.duckdns.org: `jwks` → 200, `audit/sth` → 200,
  the sibling `audit/financial` route still correctly → 401.

## BUG-008: importing uasr.mapek_worker alongside an async DB engine hangs pytest at exit
- **Status:** fixed (2026-09-02).
- **Original status note (2026-09-01, root-caused):** the `uasr.mapek_worker` /
  LLM-provider-chain import is **not** the trigger — it was a correlation
  in the original narrowing, not the cause. See the 2026-09-01 update
  below. The `recovery_persistence.py` workaround from the original
  investigation is unaffected by this update (still a reasonable module
  boundary) but is no longer necessary to avoid this hang specifically.
- **Found by:** writing tests for the Kafka MAPE-K RecoveryRecord
  persistence feature, 2026-08-31, while investigating why the pre-push
  hook for `feature/uasr-mapek-recovery-persistence` never exited despite
  its test run printing "2169 passed".
- **Severity:** cosmetic (test/dev-environment only) — but high nuisance:
  hangs the pre-push hook and any bare-script repro indefinitely, with no
  error message, only diagnosable via `ps aux` showing a live process
  past its expected exit.
- **Original root cause (2026-08-31), confirmed but not fully explained:**
  a Python process that (a) imports `uasr.mapek_worker` (even without
  constructing `MAPEKWorker`) and (b) also creates/uses an async
  SQLAlchemy engine hangs at interpreter shutdown instead of exiting —
  aiosqlite's non-daemon connection-worker threads never join, blocking
  `threading._shutdown` forever. Narrowed to "neither half alone hangs,
  but the two together do" via a dozen+ repros, but the exact mechanism
  was not isolated further.
- **2026-09-01 update — actual mechanism, fully isolated:** the
  `uasr.mapek_worker` import is a red herring. Re-tested against the
  puzzle of why `tests/test_uasr_service_cross_source_heal.py` — which
  imports `uasr.service` (itself importing `uasr.mapek_worker` at line 35
  and constructing a full `RecoveryLoop` at module scope, ~line 125, the
  same "heavy chain" the original investigation flagged as the trigger)
  plus an isolated DB engine — does *not* hang, while the bare-script
  repro with the same ingredients does. Fresh isolated repros (`aiosqlite`
  Windows/Python 3.13, run from `aurabackend/`) established, in order:
  1. A bare script identical to the original BUG-008 repro (imports
     `uasr.mapek_worker`, calls `init_uasr_db()`, opens one session) hangs
     — reconfirmed exit code 124 under a hard timeout after printing
     `"script fully done"`.
  2. The same script but *without* importing `uasr.mapek_worker` at all —
     just `metadata_store.db` + `uasr.db.init_uasr_db()` + one session —
     **also hangs**, identically. This directly falsifies "DB engine +
     session work with no `mapek_worker` import exits fine" from the
     original entry: it does not, once `init_uasr_db()` (or any real
     query) actually opens a connection.
  3. A script that creates the engine/session-factory and opens a session
     but issues *no real query* (`async with sf() as db: pass` with no
     preceding `init_uasr_db()`) exits cleanly every time, with or without
     `mapek_worker` imported. SQLAlchemy's async session connects lazily;
     with no query, no `aiosqlite.Connection` — and therefore no
     `threading.Thread(target=_connection_worker_thread)` (aiosqlite
     `core.py` line 90, **not** created with `daemon=True`) — is ever
     spawned. This is the real precondition the original investigation
     was circling: not "import X", but "did a real query open an
     aiosqlite connection."
  4. Once a connection thread exists, whether the process hangs at exit
     depends entirely on whether that thread receives aiosqlite's
     `_STOP_RUNNING_SENTINEL` before `threading._shutdown()` starts
     joining non-daemon threads. Nothing in this codebase calls
     `await engine.dispose()` before dropping engine references (neither
     the BUG-008 repro nor, in production, `metadata_store.db` itself —
     the module-level `_engine` simply lives for the process's lifetime).
     The only way the sentinel gets sent without an explicit `dispose()`
     is via `aiosqlite.Connection.__del__` (core.py line 98), which fires
     when the connection object's refcount drops to zero — and
     `AsyncEngine` → connection-pool → pooled-DBAPI-connection forms a
     reference **cycle**, so plain refcounting never frees it; it needs a
     real pass of CPython's generational cyclic garbage collector.
  5. Direct proof: taking script #1 (hangs) and adding **only** an
     explicit `gc.collect()` after nulling the module's `_engine` /
     `_session_factory` globals (no other change) makes it exit cleanly
     every time — confirmed via `threading.enumerate()` printed
     immediately beforehand, showing the worker thread still nominally
     alive at that instant yet the process still exits 0 milliseconds
     later once the sentinel is in-flight. Nulling the globals *without*
     an explicit `gc.collect()` (i.e. exactly what
     `test_uasr_service_cross_source_heal.py`'s `_isolated_metadata_db`
     fixture teardown does) is **not**, by itself, sufficient — a bare
     script doing only that still hangs.
  6. So why does the real pytest file pass reliably (3/3 fresh runs, and
     5/5 in this session's re-verification, each ~3-5s)? Because pytest's
     own ordinary object churn — test collection, fixture setup/teardown,
     `unittest.mock` patch objects, 4 test functions' worth of allocation
     — crosses CPython's default gen0/gen1 GC thresholds (confirmed via
     `gc.set_debug(gc.DEBUG_STATS)`: multiple automatic gen0 collections
     and at least one automatic gen1 collection fire during the run) many
     times over before the process exits, incidentally reclaiming the
     engine/pool cycle in time. This was verified directly: a bare script
     with the exact fixture-style null-and-no-explicit-gc pattern hangs
     when it does nothing else afterward, but exits cleanly once ~20,000
     iterations of plain cyclic-object allocation (unrelated to
     SQLAlchemy or aiosqlite — plain `_Node` objects with `self`
     references) are added after the DB work, with or without
     `uasr.mapek_worker` imported. This is not deterministic on principle
     — it depends on allocation-count timing relative to GC thresholds —
     but is deterministic *in practice* for this specific test file
     because the same fixed sequence of pytest internals runs every time.
  7. The deterministic, non-luck-dependent fix is `await engine.dispose()`
     before the last reference to the engine is dropped: confirmed via a
     repro identical to #1 except for one added line
     (`await get_engine().dispose()` before `asyncio.run()` returns) —
     `threading.enumerate()` immediately before exit shows **zero**
     non-MainThread threads (not just "about to die"), and the process
     exits cleanly every time. Independently re-verified in a fresh
     session: the exact original hanging repro (import `mapek_worker`,
     construct `MAPEKWorker`, open a session) plus one added
     `await get_engine().dispose()` line exits with code 0 every time.
  - **Conclusion:** `uasr.mapek_worker` / the LLM-provider chain never
    mattered. The real cause is generic to this codebase's pattern of
    never calling `engine.dispose()` on `metadata_store.db`'s (or any
    similar) async SQLAlchemy engine combined with aiosqlite's
    non-daemon-by-default connection worker thread (`core.py:90`) —
    identical in kind to the `loky`/`joblib` non-daemon-thread hang
    `tests/conftest.py`'s `pytest_sessionfinish` already guards against,
    and to `backend.md`'s documented `api_gateway/persistence.py` /
    `TestClient` lifespan hang. It only reliably surfaces as a hang in a
    short bare script (too little allocation churn to trigger a GC pass
    before shutdown) and only reliably *doesn't* hang inside pytest
    (enough incidental churn most of the time) — which is exactly the
    contradictory behavior this investigation was asked to explain.
    Production servers never hit this at all, since they never drop the
    engine reference while the process is still running.
- **Caused by:** none — pre-existing interaction (aiosqlite's non-daemon
  thread design + this repo never calling `engine.dispose()`), not a
  regression from any recent change.
- **Fix:** not changed as part of this update — `recovery_persistence.py`
  remains in place from the original investigation (a reasonable module
  boundary regardless) but per the finding above it was not actually
  necessary to dodge this specific hang; the true fix, if this is ever
  worth hardening beyond "add allocation churn accidentally," is an
  explicit `await get_engine().dispose()` at the end of any
  short-lived/throwaway script or fixture that creates a fresh
  `metadata_store.db` engine (mirroring what `tests/conftest.py`'s
  `pytest_sessionfinish` already does for the *long-lived* module-level
  engines it can still find a reference to) — not "avoid importing
  `uasr.mapek_worker`," which was never the actual condition. A dedicated
  test for `persist_recovery_row` was dropped in the original
  investigation "given the size of the investigation already sunk" —
  revisit now that the wall it hit is understood: any hang there would
  have been the same GC-timing race, not an `uasr.mapek_worker`-specific
  problem, so it's safe to re-attempt with an explicit `dispose()` in its
  fixture teardown instead of avoiding the import.
  the same wall.
- **Fix (2026-09-02):** swept the repo for the two qualifying call-site
  shapes above. No standalone/throwaway script against
  `metadata_store.db` was found (`alembic/env.py` already calls
  `await connectable.dispose()`; `dar_service/main.py` and
  `api_gateway/main.py` are long-lived servers, exempt per the finding).
  Two test-module fixtures were swapping `metadata_store.db`'s engine to
  a temp-file DB and restoring the original module attributes over the
  swapped-in one without disposing it first — an orphaned engine
  `tests/conftest.py`'s `pytest_sessionfinish` can no longer find, since
  by session-end the module attribute points at the pre-test value
  again — the exact `_isolated_metadata_db` gap `test_uasr_cross_source_
  heal.py` and `test_uasr_recovery_persistence.py` had already closed.
  Applied that same explicit-`dispose()`-on-a-throwaway-loop pattern to
  `aurabackend/tests/test_uasr_approval_reaper.py` and
  `aurabackend/tests/test_uasr_service_cross_source_heal.py`. Also
  re-added the `persist_recovery_row` regression test dropped in the
  original investigation — it now lives in
  `aurabackend/tests/test_uasr_recovery_persistence.py` (3 tests:
  default write, `return_row=True`, and parity between the two modes),
  whose fixture already used the explicit-`dispose()` pattern above.
  Verified: `pytest tests/test_uasr_approval_reaper.py tests/
  test_uasr_service_cross_source_heal.py tests/
  test_uasr_recovery_persistence.py --tb=short` — 9/9 passed, process
  exited immediately with no hang.

## BUG-007: test_categorical_drift_still_detected fails in CI but not locally
- **Status:** fixed
- **Found by:** CI (Backend Tests Python 3.11), PR #267, 2026-08-31 —
  unrelated to that PR's diff (`scripts/uasr_benchmark_nyc_taxi.py`, docs,
  `.gitignore`; nothing touching `uasr/drift_detector.py`).
- **Severity:** cosmetic (test infra, not product) — tracked anyway per
  the same reasoning as BUG-004: a flaky CI gate erodes trust in every
  other green run.
- **Root cause, confirmed:** `_compute_batch_embedding`
  (`aurabackend/uasr/drift_detector.py:697`) hashes each categorical
  `"col:value"` token with Python's builtin `hash()`, which is
  process-randomized (`PYTHONHASHSEED`) by design unless fixed at
  interpreter start — untestable/unfixable from inside a running test.
  The failing test's helper, `_categorical_batch`
  (`tests/test_uasr_drift_detector.py`), represented the "dominant"
  category with a single repeated token (e.g. `"cat:A"` vs `"cat:Z"`),
  so the whole drift signal rode on exactly one pair of the embedding's
  256 hash buckets. With ~1/256 (≈0.39%) probability per process, that
  one pair collides into the same bucket and the drift signal that
  cosine-distance detection relies on collapses — this is real,
  reproducible math (`256 % 1/256`), not "unconfirmed CI weirdness":
  confirmed by tracing `_compute_batch_embedding` directly and computing
  the collision probability, not by re-running until it failed again.
- **Caused by:** none — this collision risk has existed since the
  semantic-channel embedding and this test were both introduced; it is
  not a regression from any recent fix.
- **Fix:** `_categorical_batch` now spreads the dominant category over 8
  distinct tokens (`f"{dominant}_{i%8}"`) instead of one, dropping the
  chance that EVERY corresponding token pair collides to `(1/256)^8` —
  structurally immune to a single unlucky hash seed while testing the
  same behavior. Re-verified across 15 fresh Python processes (each with
  its own random hash seed, matching how CI actually varies): 15/15
  passed. Full `test_uasr_drift_detector.py` file: 42/42 passed.

## BUG-006: verify_live_deployment.py's webhooks check false-positived
- **Status:** false-positive
- **Found by:** live-verify 2026-08-31 (first run)
- **Severity:** cosmetic (test tooling, not product)
- **Root cause:** `scripts/verify_live_deployment.py`'s `check_webhooks`
  assumed the create-webhook response had `id` at the top level
  (`sub.get("id")`); the real, correct response nests it as
  `{"status": "success", "webhook": {"id": ..., ...}}`. The webhook was
  created successfully (confirmed by inspecting the raw response body in
  the failure detail) — the product worked, the check's own shape
  assumption was wrong.
- **Caused by:** none (script bug, not a regression from any prior fix)
- **Fix:** corrected inline in `scripts/verify_live_deployment.py`
  (same commit as this registry entry) — `sub.get("webhook", {}).get("id")`.

## BUG-012: DriftDetector overflows and permanently stops detecting on a near-constant column
- **Status:** fixed
- **Found by:** running the NYC-taxi benchmark's congestion-surcharge
  retry at n=20,000 rows/month (`scripts/uasr_benchmark_nyc_taxi.py`),
  2026-09-01 — not part of the original benchmark plan, a real bug
  surfaced by real-world data at scale.
- **Severity:** blocks-feature — once triggered, drift detection is
  permanently broken for the affected source (every subsequent batch
  raises `OverflowError`), not a one-off bad reading.
- **Root cause:** `aurabackend/uasr/drift_detector.py`'s location-shift
  scale guard, `scale = ref_dist.std if ref_dist.std > 0 else 1.0`, only
  catches an exact `0.0` standard deviation. A column that is truly
  constant in real data (e.g. TLC's `improvement_surcharge`, always
  `0.3`) gets a non-zero std from float rounding — `numpy.std` on N
  copies of the same `float64` returns `~5.55e-17`, not the
  mathematically-true `0`. That float-noise floor becomes the
  denominator of a "sigma" score (`loc_shift = abs(batch_dist.mean -
  ref_dist.mean) / scale`), turning any later batch's float-precision
  mean residual into an astronomically large fake sigma count. That
  fake value feeds `kl_history`, which the adaptive threshold reads back
  on every subsequent batch — confirmed geometric growth across 45
  batches (`8.1e13 → 8.3e25 → 1.8e38 → ... → 2.5e159`) until it overflows
  a Python float.
- **Caused by:** none — pre-existing float-precision gap, not a
  regression from any recent change; only surfaced now because this was
  the first real-data run at large enough scale/enough batches to
  compound the error into an overflow.
- **Fix:** floor the guard at the module's existing `_EPS = 1e-10`
  constant (same epsilon already used for the KL calculation) instead of
  `0` — `scale = ref_dist.std if ref_dist.std > _EPS else 1.0`. A std
  indistinguishable from float noise is now treated as the exact-zero
  case already handled. Re-verified against the same 20,000-row replay
  that originally crashed (now completes cleanly, max KL 22.88); 2 new
  regression tests in
  `aurabackend/tests/test_uasr_drift_detector.py::TestNearConstantColumnFeedbackOverflow`.

## BUG-013: approval-queue escalation and per-tenant repair fairness are implemented but not armed in production
- **Status:** fixed (config only — requires a redeploy to take effect,
  see note)
- **Found by:** live-verification of gap-analysis candidates #3/#4
  (`docs/superpowers/specs/2026-08-30-uasr-effective-self-healing-gap-analysis.md`),
  2026-09-01, via `GET /uasr/deployment` config introspection against
  `https://dataaura.duckdns.org`.
- **Severity:** degrades-accuracy — the code path is correct and tested,
  but its safety guarantee (S41's human-in-the-loop queue actually gets
  escalated; one noisy source can't starve every other source's repair
  budget) was silently inert in the one place it matters, production.
- **Root cause:** `deploy/aws-free-tier/docker-compose.yml` never set
  `UASR_APPROVAL_TIMEOUT_SECONDS` or `UASR_REPAIR_MAX_PER_SOURCE` in the
  `uasr_service` environment block. Both default to `0` (off) per
  `runtime_config.py`'s deliberate opt-in-by-default-off convention —
  correct as a library default, but nobody ever flipped them on for the
  actual deployment. Live confirmation: `GET /uasr/deployment` returned
  `"approval_timeout_seconds":0,"repair_max_per_source":0`. Per
  `service.py`, the approval-timeout reaper task is only started when
  `approval_timeout_seconds() > 0` — on the live box it was simply never
  running, so any `PENDING_APPROVAL` recovery would have waited forever
  (S41's own human-in-the-loop guarantee undermined by an unwatched
  queue), and the repair scheduler's per-source cap was `0` = no ceiling,
  so a single source's drift storm could consume the entire 4-slot
  global repair budget.
- **Adjacent finding, not fixed here:** the same introspection call
  returned `"risk_tiered":false`. `PENDING_APPROVAL` is only reachable
  when risk-tiering is on (`recovery_loop.py`), so with it off,
  recoveries may never reach `PENDING_APPROVAL` in the first place —
  meaning even with the reaper now armed, there is currently nothing for
  it to escalate. Turning on `risk_tiered` is a materially bigger
  behavior change (every non-deterministic-template fix starts requiring
  human approval, not just a reachability flag) and needs its own
  explicit decision rather than being bundled into this fix.
- **Caused by:** none — a deployment-config gap, not a code regression;
  both features were correctly implemented and tested, just never wired
  into the one running deployment's environment.
- **Fix:** `deploy/aws-free-tier/docker-compose.yml` now sets
  `UASR_APPROVAL_TIMEOUT_SECONDS=1800` (30 min — long enough for a
  genuine human review window, short enough to actually escalate) and
  `UASR_REPAIR_MAX_PER_SOURCE=2` (half the 4-slot global concurrent
  budget, leaving headroom for at least one other source). **This
  registry entry and the compose-file change do not themselves change
  live behavior** — per BUG-005's precedent, the running container needs
  an operator-run `docker compose pull && docker compose up -d` (or
  equivalent redeploy) on the box for the new env vars to take effect;
  re-verify via `GET /uasr/deployment` after redeploying.

## BUG-014: test_demo_endpoints.py had 4 failures in one full-suite pre-push run, unreproducible since
- **Status:** unconfirmed — investigated, could not reproduce; documented
  rather than silently dropped, per this registry's own process.
- **Found by:** the `fix/uasr-cross-source-heal-fanout` branch's pre-push
  hook, 2026-09-01. Full run: `4 failed, 2199 passed, 22 skipped` in
  5113.89s (1h25m13s) — `test_unknown_scenario_404` (`assert 401 == 404`),
  `test_demo_cold_cache_returns_503_not_blocking_audit`
  (`assert 401 == 503`), `test_demo_serves_prewarmed_artifact_instantly`
  (`assert 401 == 200`), `test_another_tenant_cannot_read_this_tenants_job`
  (`KeyError: 'job_id'` — downstream of the same 401: the POST response
  has no `job_id` key when auth fails).
- **Severity:** cosmetic (test infra) unless it recurs and turns out to be
  real — see Fix/next-steps below.
- **Investigation:** `test_demo_endpoints.py`'s module-level `client =
  TestClient(app, headers=_auth())` (line 23) signs its JWT once at
  collection time via `shared.auth.create_access_token`, reading
  `settings.secret_key`. All 4 failures are consistent with that token
  later failing signature/mode verification (401) at request time.
  Ruled out: (1) `test_demo_endpoints.py` passes 11/11 standalone,
  repeatedly, both in the branch's own working tree and in a clean
  worktree; (2) a dedicated investigation ran the exact 50-file prefix
  that precedes `test_demo_endpoints.py` in collection order (confirmed
  identical file set in both the branch's tree and a clean `main`
  worktree — none of this branch's new/changed files fall in that
  prefix) plus `test_demo_endpoints.py` itself: 684 passed, 0 failed,
  clean; (3) no `pytest-randomly` or custom
  `pytest_collection_modifyitems` hook is installed/configured
  (`pyproject.toml`, `tests/conftest.py` checked) — collection order is
  deterministic, so this isn't seed-dependent flakiness in the usual
  sense; (4) **the exact same full-suite command, run again from a clean
  worktree on a slightly older commit, passed 100% clean — `2165 passed,
  22 skipped, 0 failed` in 15m06s** — over 5x faster than the original
  failing run's 1h25m13s wall time. That last point is the strongest
  signal: this environment showed heavy, variable slowdown this session
  (a different branch's own pre-push run separately took ~80 minutes to
  reach 22% before suddenly catching up), and this repo already has a
  documented hazard in the exact same family — BUG-008's aiosqlite
  non-daemon connection-thread / GC-timing race, and `conftest.py`'s own
  `pytest_sessionfinish` comment about a CI runner once wedging for
  6+ hours with zero diagnostic output. A thread-scheduling or
  resource-contention race under heavy system load, not a deterministic
  state leak, best fits every data point gathered: reproducible only
  once, not reproducible on retry at the same or larger scale, no seed
  dependence, no leaking-test candidate found despite a real dynamic
  bisection effort.
- **Caused by:** none confirmed — see above; if a future recurrence
  pins it to a real leak, update this entry rather than filing a
  duplicate.
- **Fix:** none applied — there is nothing to fix without a reproduction.
  Left `open`-in-spirit/unconfirmed rather than closed: if this recurs,
  the next investigation should start from the two ruled-out mechanisms
  above (state leak, seed dependence) and instead pursue a timing/race
  hypothesis — e.g. capture `faulthandler`/thread-stack output
  (`faulthandler_timeout = 1200` is already configured in
  `pyproject.toml`) on the next failing run, and correlate with system
  load at the time.

## BUG-015: live deployment was 15 days / 127 commits stale, no deploy process caught it
- **Status:** fixed
- **Found by:** the BUG-013 redeploy, 2026-09-02 — `git log HEAD..origin/main`
  on the box returned 127 commits, not the handful expected from "apply one
  config fix."
- **Severity:** blocks-feature (indirectly) — every merged fix/feature since
  2026-08-18 was silently inert on the one deployment real users hit, with
  nothing anywhere signaling that.
- **Root cause:** three compounding gaps, plus one real incident:
  1. **No CI/CD auto-deploy.** `.github/workflows/cd.yml` builds and pushes
     `latest-*` images to GHCR on every merge to `main` but never touches the
     box — a human has always had to SSH in and pull.
  2. **No working scheduled redeploy either.** `crontab` was not even
     installed on the box (`which crontab` → not found), so no cron job —
     including the one this README already documented for `backup.sh` — was
     ever actually running. That documented cron line also named the wrong
     path (`/home/ec2-user/AURA`, the deploy actually lives at `/opt/aura`),
     so it would have failed even if crontab had existed.
  3. **The manual redeploy runbook was two independent, unlinked steps**
     (`git pull` the repo for `docker-compose.yml`/`Caddyfile` changes;
     `docker compose pull && up -d` for new images) with nothing tying them
     together or verifying both happened.
  4. **The incident this produced, reconstructed from `.env` backups on the
     box:** on 2026-08-31 17:56 UTC someone did a partial fix — switched
     `AURA_TAG` from a pinned `0.1.4` to `latest` (confirmed via
     `diff .env.bak.20260831T175603Z-pre-latest .env`, single-line change)
     — but never ran the matching `git checkout`. The box's
     `docker-compose.yml` stayed frozen at commit `0c4bd76`
     (2026-08-18), so even after the tag fix, `docker compose pull && up -d`
     could only ever produce containers running new *code* inside old
     *orchestration* — a service or env var added after 2026-08-18 (e.g.
     BUG-013's own `UASR_APPROVAL_TIMEOUT_SECONDS`) had no way to exist on
     the box no matter how many times the image-pull half was repeated.
- **Caused by:** none — pre-existing process gap; the 2026-08-31 incident is
  a symptom of it, not a separate regression.
- **Fix:** brought the box to `origin/main` (`git checkout` + `docker compose
  pull && up -d`), re-verified live: `GET /uasr/deployment` now reports
  `approval_timeout_seconds: 1800`, `repair_max_per_source: 2` (BUG-013's
  fix, finally armed); full `scripts/verify_live_deployment.py` sweep 12/12
  passed post-redeploy. Added `deploy/aws-free-tier/redeploy.sh` — a single
  atomic script (fetch → checkout → image pull → recreate → health-check,
  `set -euo pipefail` throughout) replacing the two-step manual sequence so
  a future redeploy can't be done "half" the way 08-31's was; updated
  `README.md`'s Operating/Rollback sections to reference it and corrected
  the stale `AURA_TAG=latest` vs. "never latest" rollback claim and the
  wrong cron path. **Not fixed, and worth a follow-up decision:** there is
  still no automated trigger (CI/CD push-to-box, or even a working cron) —
  a redeploy remains a manual `./redeploy.sh` run; whether to add real CI/CD
  (needs SSH credentials as a GitHub secret — a security-posture decision)
  or just get `crontab` installed and this script scheduled is an explicit
  open question, not silently resolved by this fix.

## BUG-010: four undocumented silent-stub call sites (zero-stub-compliance audit)
- **Status:** partially fixed. Item 1 (the only reachable one) fixed
  2026-09-02 — see its entry below. Items 2-4 remain open but are
  currently unreachable / already honestly disclosed, per each item's
  own note. Filed so it doesn't evaporate as an unlogged agent report,
  per this registry's own process.
- **Found by:** dedicated zero-stub-compliance audit of `aurabackend/`
  (excluding tests/migrations), 2026-09-01, cross-checked against
  `STATUS.md`/`README.md`/`docs/DEPLOYMENT.md`.
- **Severity:** mixed, see each item below.
- **Findings:**
  1. **`aurabackend/pipeline/engine.py:421-649`** (`_step_to_sql`) — no
     branch for `StepType.UNION` (a real enum member,
     `aurabackend/pipeline/models.py:58`). A pipeline step of type
     `"union"` silently falls through to `return None`, which the caller
     (`engine.py:402-405`) treats as "skip," incrementing
     `steps_skipped` but never erroring or messaging the user. The LLM
     pipeline *generator* is told not to emit union steps
     (`pipeline/generator.py:85`), but nothing stops a hand-built or
     API-submitted pipeline from silently no-oping one — a real
     silent-wrong-result path, undocumented anywhere top-level.
     **Severity: degrades-accuracy, reachable via the pipeline API today.**
  2. **`aurabackend/scheduler_service/executor.py:383-385`**
     (`_calculate_next_execution`) — `ScheduleType.CRON` (a documented,
     API-exposed option) isn't parsed; any cron-scheduled job silently
     runs hourly instead. Disclosed in the scheduler service's own
     `README.md`/`IMPLEMENTATION.md` (labeled "Placeholder"/"Future
     Enhancement" needing `croniter`), but not in top-level docs.
     **Mitigated: `STATUS.md` already states the scheduler service has no
     gateway route in the current deployment, so currently unreachable
     by an end user — real gap, currently inert.**
  3. **`aurabackend/scheduler_service/distributed_queue.py`** — its own
     module docstring says these primitives (leader election,
     LISTEN/NOTIFY) are "NOT YET wired into `worker.py`." Honestly
     labeled in-repo, infra-only, no user-visible impact in a
     single-worker deployment. **Severity: cosmetic for now.**
  4. **`aurabackend/ingestion_service/main.py:45-52,138-148`**
     (`map_erc_to_internal_id`) — explicitly commented
     `# --- ERC Mapping Logic (Mock/Stub for Phase 1) ---`; the wired
     endpoint `GET /api/v1/ingest/erc-map/{erc}` fabricates an ID
     (`f"AURA-NORM-{system_origin}-{erc}"`) with no real lookup.
     **Mitigated: `STATUS.md` states `ingestion_service` never starts in
     the current deployment — currently unreachable.**
- **Caused by:** none — pre-existing across a long project history, not a
  regression from any recent change.
- **Fix:** Item 1 fixed 2026-09-02 — `PipelineEngine._step_to_sql`'s final
  fall-through (`aurabackend/pipeline/engine.py`, previously a bare
  `return None`) now raises `ValueError(f"Pipeline step type {t.value!r}
  is not implemented")` instead. The caller's existing top-level
  `except Exception` (engine.py:197-200) turns this into
  `run.status = FAILED` with a real `run.error` message — a UNION step
  submitted directly to the pipeline API now fails loudly instead of
  silently no-oping and returning SUCCESS with the step dropped. Full
  UNION SQL generation was NOT implemented (no schema exists yet for
  which source/columns to union — a real product decision, not a
  mechanical one) — this closes the silent-wrong-result path only.
  Regression test: `test_union_step_fails_run_instead_of_silently_skipping`
  in `aurabackend/tests/test_pipeline_execution.py`. Items 2-4 are
  honestly disclosed already or currently unreachable; revisit if the
  scheduler/ingestion services become reachable.

## BUG-016: webhooks.py + inbound_hooks.py CRUD has zero tenant scoping (cross-tenant IDOR)
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02 (13-agent subsystem sweep + adversarial verify pass over the whole repo, `wf_74a1105c-a1e`).
- **Severity:** blocks-feature (security) — any authenticated caller can enumerate/read/edit/delete/test-fire any OTHER tenant's outbound webhook subscriptions and inbound hooks.
- **Root cause:** `aurabackend/api_gateway/routers/webhooks.py` (create/list/get/patch/delete/test, lines 83-148) and `inbound_hooks.py`'s CRUD routes (lines 63-107) call the module-level `webhook_dispatcher`/`inbound_hooks` singleton registries keyed only by bare `sub_id`/`hook_id`, unlike every other stateful router in this package (`connections.py`, `dashboards.py`, `chat.py`, `pipelines.py`, `files.py`, `queries.py`) which derives `workspace_id`/tenant via `current_workspace_id()`/`_request_tenant()` and filters every read/write by it. `shared/webhook_dispatcher.py` and `shared/inbound_hooks.py`'s underlying stores have no workspace/tenant field at all — this is a direct regression against security.md's "Least-privilege scope" rule, not a documented tradeoff.
- **Caused by:** none — pre-existing since these routers were added.
- **Fix:** added a `workspace_id` field to `WebhookSubscription`, `DeliveryRecord`, and `InboundHook`. `WebhookDispatcher.list/get/register/update/delete/deliveries/fire_test` and `InboundHookRegistry.list/get/register/update/delete` now take/filter by `workspace_id`, resolved per-request via the established `current_workspace_id(request)` helper (`api_gateway/routers/workspaces.py`, the same chokepoint `connections.py`/`dashboards.py` use). Every route in `webhooks.py` and the CRUD routes in `inbound_hooks.py` now take a `Request` and pass the resolved workspace through; a cross-tenant id 404s instead of returning/mutating another tenant's record. `InboundHookRegistry.by_slug()` (used only by the public `POST /hooks/fire/{slug}` trigger) is intentionally left unscoped — its auth boundary is the hook's own optional HMAC secret, not the caller's tenant; see BUG-017. Tests: `test_webhook_dispatcher.py`/`test_inbound_hooks.py`'s `TestCrossTenantIsolation` classes; 95/95 tests pass across `test_webhook_dispatcher.py`, `test_inbound_hooks.py`, `test_middleware.py`.

## BUG-017: inbound_hooks fire_hook not in JWT public-path allowlist — blocks its own intended external HMAC callers
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature — the inbound-hooks feature is unreachable by the external systems it's built for on any deployment with JWT auth armed (the production default; `config.py`'s `_require_jwt_for_tenant_isolation_in_production` validator makes `AURA_JWT_ENABLED=true` mandatory, not optional, in production).
- **Root cause:** `POST /api/v1/hooks/fire/{slug}` (`inbound_hooks.py:122-135`) takes no `Depends(require_user)` and is meant to be gated solely by its own optional per-hook HMAC secret check — but it is absent from `shared/middleware.py`'s `_PUBLIC_PATHS`/`_PUBLIC_PATH_PREFIXES` allowlist, so `JWTAuthMiddleware` 401s any caller with no AURA Bearer token before the handler (and its HMAC check) ever runs. This is the identical failure class already root-caused once for a different route (see this file's own BUG-005 history: "the global auth gate silently defeated the feature on every deployment with auth correctly armed") but was not applied here. `tests/test_inbound_hooks.py` never wires `JWTAuthMiddleware` into its test app, so CI stays green while the feature is unreachable in the one mode it exists for.
- **Caused by:** none — same class as BUG-005 but a distinct route, not a regression from that fix.
- **Fix:** added `"/api/v1/hooks/fire/"` to `_PUBLIC_PATH_PREFIXES` in `shared/middleware.py` (prefix match, since `{slug}` varies — same mechanism BUG-005 used). This also exempts the route from `APIKeyMiddleware` (correct — no AURA credential to present) but NOT from `RateLimitMiddleware` (keys off the separate exact-match set), so it keeps its default per-IP throttling. Tests: `test_middleware.py::TestJWTMiddleware::test_fire_hook_reaches_handler_without_a_token` and `::test_fire_hook_is_not_gated_by_a_bearer_token` — both drive a stand-in route through a real, genuinely-installed `JWTAuthMiddleware` with no Bearer token, asserting it reaches the handler (404) rather than 401ing.

## BUG-018: metadata_store HTTP service has zero tenant scoping on semantic-model / dataset-profile / user endpoints
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature (security) — every semantic model/dataset profile created or read through this service's own HTTP surface (port 8007) is pinned to `workspace_id=None`, a cross-tenant blind spot; `get_user` returns any user's name/email for any `user_id` with no identity check.
- **Root cause:** `aurabackend/metadata_store/main.py`'s `list_semantic_models`/`get_semantic_model`/`upsert_semantic_model`/`get_dataset_profile` (lines 63-189) call the repository layer without ever passing `workspace_id`, even though `repository.py` (lines 172, 248, 267) added that exact parameter specifically to close a documented prior cross-tenant leak. The fix landed in the repository layer and is correctly used by the gateway's in-process callers (`api_gateway/routers/pipelines.py`, deriving `current_workspace_id(request)` from the verified JWT) — but this standalone HTTP service was never updated to match, and its own JWT/API-key auth is only opt-in (`AURA_JWT_ENABLED=true`), so with auth off this is an unauthenticated, tenant-blind duplicate of the gateway's API.
- **Caused by:** none — the repository-layer fix (already shipped) was applied to one caller (the gateway) and not this one, not a regression it introduced.
- **Fix:** `aurabackend/metadata_store/main.py` — added `_workspace_id(request)`, mirroring `api_gateway/routers/workspaces.py::current_workspace_id`: uses the verified JWT's `org_id`/`sub` from `request.state.user` (stashed by `JWTAuthMiddleware` when `AURA_JWT_ENABLED`) when present, else falls back to the `X-Workspace-Id` header (dev/open mode), else `None` (fail-closed — matches only pre-tenanting rows). Threaded into `list_semantic_models`, `get_semantic_model`, and `upsert_semantic_model`. `get_dataset_profile`/`get_user` were not part of this fix: their repository methods don't accept a `workspace_id` parameter at all (`dataset_profiles` has no tenant column yet — the same documented gap noted in `pipelines.py`'s `get_dataset_profile` route). Tests: `aurabackend/tests/test_metadata_store.py` — `TestRepositoryTenantIsolation`, `TestWorkspaceIdHelper`, `TestSemanticModelRoutesPassWorkspaceScope`. 46/46 tests pass; `test_tenant_isolation.py` (gateway-side precedent) re-run clean, 6/6.

## BUG-019: evolution subsystem has zero tenant scoping, an unvalidated status-bypass, and an unbounded rating field
- **Status:** open
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature (security + data-integrity) — three related gaps in `aurabackend/evolution/`:
  1. `models.py`'s four tables (`ExecutionPattern`, `ImprovementProposal`, `SystemEvolutionLog`, `AgentFeedback`) have no `tenant_id` column, and none of `api.py`'s GET routes (`/patterns`, `/proposals`, `/proposals/{id}`, `/log`, `/feedback/summary`, lines 114-262) filter by tenant — any authenticated caller sees every other tenant's prompts, agent output, and pattern stats. `POST /evolution/feedback` also accepts an arbitrary `session_id` with no ownership check.
  2. `PATCH /evolution/proposals/{id}` (`api.py:61,197`) writes the client-supplied `status` string straight onto the ORM row with no validation against the `ImprovementStatus` enum, letting a caller set a proposal to `"deployed"` directly, bypassing the confidence-threshold deploy gate `engine.py`'s own docstring promises ("never deploys changes destructively... only when confidence ≥ threshold").
  3. `FeedbackRequest.user_rating` (`api.py:50`) has no `Field(ge=1, le=5)` despite being documented and stored as a 1-5 rating (`models.py:107`), so out-of-range values silently corrupt feedback aggregates.
- **Root cause:** this subsystem was never brought in line with the tenant-isolation and schema-validation invariants (security.md, backend.md) the rest of the codebase enforces.
- **Caused by:** none — pre-existing since the subsystem was added.
- **Fix:** add a `tenant_id` column (Alembic migration) to all four tables and filter every read/write by it; change `ProposalUpdateRequest.status` to the `ImprovementStatus` enum/`Literal` type instead of bare `str`; add `Field(ge=1, le=5)` to `user_rating`. Not yet fixed — the tenant-scoping half needs a migration, larger than the other two one-line Pydantic fixes.

## BUG-020: SQL-injection sweep — 7 call sites splice identifiers/values into raw SQL instead of the one shared quoter
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature (security) for the reachable ones; the repo already treats this class as HIGH per its CodeQL history (security.md, Sec-2..Sec-8).
- **Root cause:** backend.md mandates one shared quoter (`shared/sql_identifiers.py::quote_identifier`/`quote_literal`) for exactly this reason ("LLM-generated column names get spliced into raw DDL... 68 call sites unprotected" was the last time this happened repo-wide). Seven more sites were found still hand-rolling or skipping it entirely:
  1. `aurabackend/pipeline/engine.py:733` `_write_pg_sink` splices `sink.table` (raw pipeline-definition input) unquoted into `DROP TABLE`/`CREATE TABLE`/`INSERT INTO` — reachable via the pipeline API. The sibling `_write_duckdb_sink` two functions below does this correctly.
  2. `aurabackend/pipeline/engine.py:285` `_load_db_source` hand-escapes literal values via manual quote-doubling in a per-row insert loop instead of bound parameters — the sibling `_load_kafka_source` right below does this correctly with `?` placeholders.
  3. `aurabackend/dar_service/main.py:87` `/dar/research/run` accepts a caller-supplied `duckdb_path` with zero validation (opens ANY DuckDB file reachable on the host) and splices `table_name` unescaped into f-string SQL in `graph.py`.
  4. `aurabackend/connectors/postgresql_connector.py:132,167` (and the same pattern in `mysql_connector.py`, `duckdb_connector.py`) splice `table_name` unquoted; reachable unvalidated via `/connectors/{type}/profile` (one of two call sites bypasses even the ad-hoc `_IDENT_RE` regex guard the sibling materialize/preview endpoints apply).
  5. `aurabackend/orchestration_service/agents/generator_agent.py:133-147`'s deterministic SQL fallback (fires whenever the LLM is unavailable — not a rare path) splices `table_name`/columns parsed from free-text schema context, unquoted.
  6. `aurabackend/mcp_servers/aura_mcp_server.py:205` `duckdb_sample_table` hand-rolls `f'SELECT * FROM "{table}"'` instead of `quote_identifier`.
  7. `aurabackend/shared/database_adapter.py:296-352` (`vector_search`/`store_vector`/`store_point`) splices table/column names unquoted — not exploitable today (only caller, `vault_client.py`, passes hardcoded literals) but the shared adapter itself provides no protection the moment any caller passes a dynamic name.
- **Caused by:** none — same recurring class as the original 68-site sweep, not a regression from a recent change.
- **Fix:** ran every identifier above through `quote_identifier`, values through bound parameters/`quote_literal`. `aurabackend/pipeline/engine.py` — `_write_pg_sink` (`DROP`/`CREATE`/`INSERT`, and each column def) and `_load_db_source` (per-row insert loop switched to `?` bound parameters, mirroring `_load_kafka_source`). `aurabackend/dar_service/graph.py` — `introspect_node`/`profile_node` DuckDB DESCRIBE/SELECT/COUNT splices now quote `state.table_name` and `col.column`; `aurabackend/dar_service/main.py` — `duckdb_path` now resolved through `shared/safe_paths.py::safe_join` against a configured `AURA_ANALYTICS_LAKE_DIR` (default `data`) instead of accepting an arbitrary absolute path. `aurabackend/connectors/postgresql_connector.py`, `mysql_connector.py`, `duckdb_connector.py` — `sample_rows`/`profile_table`/`get_table_schema` now quote `table_name`. `aurabackend/orchestration_service/agents/generator_agent.py` — the deterministic SQL fallback now quotes `table_name` and every column parsed from schema context. `aurabackend/mcp_servers/aura_mcp_server.py` — `duckdb_sample_table` now uses `quote_identifier` instead of hand-rolled `f'"{table}"'`. `aurabackend/shared/database_adapter.py` — `vector_search`/`store_vector`/`store_point` now quote `table`/`column`/`geom_column`/data-derived column names. Regression tests added: `tests/test_pipeline_execution.py::test_pg_sink_quotes_table_name_containing_double_quote` (mocked PG pool, asserts a `"` in `sink.table` comes out safely doubled-quoted, never spliced raw) and `tests/test_connectors.py::TestDuckDBConnectorIdentifierQuoting` (table name containing `"` round-trips through `sample_rows`/`profile_table`/`get_table_schema`). Full touched-area test run: 190 passed (`test_pipeline.py`, `test_pipeline_execution.py`, `test_connectors.py`, `test_connectors_duckdb_spatial.py`, `test_dar_service.py`, `test_orchestration.py`, `test_mcp_core.py`, `test_mcp_servers.py`, `test_database_adapter.py`, `test_connector_registry.py`), plus `test_chat_pipeline.py`/`test_connectors_faiss.py` (19 passed, 1 skipped) — all green; `ruff check` clean.

## BUG-021: uasr distributed-repair + Redis state store block the single uvicorn worker on synchronous Redis I/O
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature — under `UASR_REPAIR_BACKEND=distributed`/`UASR_STATE_BACKEND=redis` (the documented fleet-mode config), every drift repair submission and every `/uasr/ingest`, `/uasr/heal`, `/uasr/baseline` call blocks the sole uvicorn worker on real network round-trips, freezing every concurrent tenant's request for the duration.
- **Root cause:** `runtime_config.build_redis_client()` (`runtime_config.py:164`) returns the synchronous `redis.Redis.from_url(url)` client (not `redis.asyncio`), used directly with no `asyncio.to_thread` offload by `DistributedRepairCoordinator._try_admit`/`_prune_expired`/`_enqueue`/`_heartbeat` (`distributed_repair.py`, ~8 sequential calls per invocation) and by `RedisStateStore.load`/`save`/`contains`/`delete`/`source_ids` (`state_store.py:245-273`, called synchronously from `DriftDetector.detect()`/`.register_baseline()`, themselves called directly from the async `ingest_batch`/`heal_batch`/`register_baseline` handlers in `service.py`). `source_ids()` additionally uses the blocking O(N) `KEYS` command instead of `SCAN`, which also blocks Redis server-side for every other client sharing that instance. Direct violation of backend.md's async-safety rule.
- **Caused by:** none — pre-existing since the Redis fleet-mode backends were added.
- **Fix:** two different approaches, chosen per call chain depth:
  1. `aurabackend/uasr/distributed_repair.py` — `DistributedRepairCoordinator`'s internal methods (`_try_admit`, `_prune_expired`, `_enqueue`, `_acquire_lock`, `_release_lock`, `_heartbeat`, `_release`) and its three public observability methods (`active_count`, `queue_depth`, `active_count_for_source`) are now `async def`, each blocking `self._r.*` call wrapped in `await asyncio.to_thread(...)`. This was tractable as a full async-ify (option a from the task brief) because every caller — `submit()`/`_heartbeat_loop()` internally, plus every external caller found by grep — already runs inside an `async def`/`asyncio.run()` context, so propagating `await` was a contained, mechanical change with no synchronous callers to break.
  2. `aurabackend/uasr/state_store.py` — kept `RedisStateStore`'s `load`/`save`/`contains`/`delete`/`source_ids` **synchronous** (chose option b instead). `DriftDetector` (`drift_detector.py`) exposes four read-only back-compat **properties** (`_baselines`, `_schema_baselines`, `_reference_embeddings`, `_kl_history`) built from `self._store.source_ids()`/`.peek()`, consumed not just by `service.py` but also `numeric_semantics.py`, `martingale.py`, and several test files — properties can't be `await`ed, so async-ifying `StateStore` would have forced a much larger, riskier rewrite of `DriftDetector`'s public surface (and `InMemoryStateStore`'s trivial sync methods) for a store whose I/O is opt-in (Redis backend only). Instead, wrapped at the outermost async-handler call sites in `aurabackend/uasr/service.py`: `ingest_batch`/`heal_batch` (`_detector.detect(batch)`, both call sites), `register_baseline` (`_detector.register_baseline(...)`), and `declare_schema_intent` (`_detector.declare_schema_intent(...)`) now all go through `await asyncio.to_thread(...)`, which offloads the entire synchronous chain — including `RedisStateStore`'s blocking `self._r` calls — off the event loop. Also replaced `source_ids()`'s `self._r.keys(...)` with `self._r.scan_iter(...)` (still synchronous, called from the now-offloaded thread) per the root cause's SCAN note.
  Verified neither `DistributedRepairCoordinator` nor the `DriftDetector`/`RedisStateStore` chain relies on the calling thread's context (`get_event_loop`/`get_running_loop`) — safe to move to a worker thread via `to_thread`.
  Test fallout from (1): `test_uasr_distributed_repair.py`/`test_uasr_redis_live_server.py` updated to `await` the now-async coordinator calls (`_enqueue`, `_try_admit`, `active_count`, `active_count_for_source`) — all were already inside `async def run()`/`asyncio.run()` bodies, so no synchronous test needed restructuring. `test_per_source_cap_prevents_starvation_across_nodes`'s timing margins (work-sleep, pre-sleep) were widened: `asyncio.to_thread` adds real thread-pool dispatch overhead across 11 concurrently-polling `submit()` loops, which the original 0.02s/0.03s margins (tuned for an instant in-process fakeredis call) no longer left enough headroom for. Fallout from (2): `test_uasr_state_store.py`'s hand-rolled `_FakeRedis` test double gained a `scan_iter` method mirroring its existing `keys`. Verified: `tests/test_uasr_distributed_repair.py`, `tests/test_uasr_redis_live_server.py`, `tests/test_uasr_state_store.py`, `tests/test_pipeline_execution.py`, `tests/test_code_generation.py`, `tests/test_orchestration.py`, `tests/test_uasr_service_cross_source_heal.py`, `tests/test_uasr_drift_detector.py`, `tests/test_uasr_schema_intent.py`, `tests/test_uasr_gateway_facade_coverage.py` — 132 passed, 4 skipped (no live/fakeredis), 0 failed.

## BUG-022: three more services call blocking LLM/file-load code directly from async handlers with no to_thread offload
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature — each freezes its single uvicorn worker for every concurrent caller for the duration of the blocking call.
- **Root cause:** three call chains never got the `asyncio.to_thread` wrapping backend.md requires for exactly this pattern:
  1. `aurabackend/pipeline/engine.py:220` `_load_source` dispatches FILE sources to `_load_file_source` (`smart_load_file(..., use_llm=True)` — a network LLM call), DUCKDB sources to `_load_duckdb_source` (ATTACH + CREATE TABLE AS on a large external file), and DB sources to a synchronous per-row insert loop — none offloaded, even though every other `conn.execute` in the same `execute()` function IS wrapped with an explicit comment explaining why.
  2. `aurabackend/code_generation_service/main.py:107` `/generate_code` calls `CodeGenerationEngine.generate()` synchronously, which calls a synchronous `shared/llm_provider.py` provider method doing a blocking `httpx.post` (up to `AURA_LLM_TIMEOUT`, default 120s).
  3. `aurabackend/orchestration_service/main.py:78` calls `TinyRecursiveCoordinator.execute()` synchronously, chaining up to 6 sequential blocking LLM calls (generator + critic, up to `max_depth=3` rounds).
- **Caused by:** none — pre-existing.
- **Fix:** wrapped each call site directly at the async handler boundary (per-call-site wrap, no signature changes to the wrapped functions):
  1. `aurabackend/pipeline/engine.py`'s `_load_source` now dispatches FILE/DUCKDB sources through `await asyncio.to_thread(self._load_file_source, ...)`/`await asyncio.to_thread(self._load_duckdb_source, ...)`; `_load_db_source`'s `CREATE TABLE`+per-row `INSERT` loop was moved into a local closure and wrapped in a single `await asyncio.to_thread(_create_and_insert)` so the whole loop runs off-loop in one dispatch rather than per-row.
  2. `aurabackend/code_generation_service/main.py`'s `/generate_code` handler now calls `await asyncio.to_thread(_engine.generate, step)`.
  3. `aurabackend/orchestration_service/main.py`'s `/v1/orchestrations/query` handler now calls `await asyncio.to_thread(coordinator.execute, request)`.
  Verified none of `smart_load_file`, `_load_duckdb_source`, `_load_db_source`, `CodeGenerationEngine.generate`/`shared/llm_provider.py`, or `TinyRecursiveCoordinator.execute`/`coordinator.py` call `asyncio.get_event_loop()`/`get_running_loop()` — safe to run off the calling thread. Verified: `tests/test_pipeline_execution.py`, `tests/test_code_generation.py`, `tests/test_orchestration.py` — 30 passed, 0 failed.

## BUG-023: scheduler_service — a variable-shadowing crash in error handling, and an unvalidated schedule_config crash
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature.
- **Root cause:** two independent bugs in the same service:
  1. `aurabackend/scheduler_service/main.py:416-431` `list_executions`'s `status: Optional[JobStatus] = None` parameter shadows the module-level `from fastapi import ... status` import for the whole function body; its own `except Exception` block then does `status_code=status.HTTP_500_INTERNAL_SERVER_ERROR`, which resolves to the local parameter (`None` or a `JobStatus` value, not the fastapi module) — so any exception this endpoint hits raises `AttributeError` instead of returning a structured 500.
  2. `aurabackend/scheduler_service/executor.py:344-381` `_calculate_next_execution`'s daily/weekly/monthly branches feed unvalidated `schedule_config` values (`day`/`hour`/`minute`) straight into `datetime.replace()` with no bounds check. Since this call happens inside `execute_job`'s SUCCESS path (right after a query already succeeded and its results were stored), a `ValueError` here (e.g. `day=31` in a 30-day month) is caught by the outer generic exception handler and the run is retried/reported as FAILED — a successful query silently misreported.
- **Caused by:** none — pre-existing.
- **Fix:** renamed the `status` parameter to `status_filter` in `list_executions` (`aurabackend/scheduler_service/main.py`); updated the gateway's `scheduler_list_executions` proxy (`aurabackend/api_gateway/routers/pipelines.py`) to forward its own `status` query param as `status_filter` downstream so the rename doesn't silently break status filtering through the gateway. Added a `_validate_schedule_config` field validator on `CreateJobRequest`/`UpdateJobRequest` bounding `day` (1-31), `day_of_week` (0-6), `hour` (0-23), `minute` (0-59) — an out-of-range value now fails job creation/update with a clear 422 instead of crashing a later successful execution. Also added a defense-in-depth clamp in `_calculate_next_execution`'s monthly branch (`aurabackend/scheduler_service/executor.py`) to the current month's actual last day via `calendar.monthrange`, since a `day=31` in a 30-day month is in-bounds but still not `datetime.replace()`-safe. Tests: `aurabackend/tests/test_scheduler_bug023.py` (new — 6 tests covering the shadowing crash, the renamed query param, schedule_config bounds validation, and the monthly-clamp); full `test_scheduler_*` suite still green.

## BUG-024: three routers leak raw exception text to the client instead of routing through sanitize_error
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** degrades-accuracy (info leak) — DB-driver/DuckDB error text can carry file paths, table/column names, connection strings, or query fragments.
- **Root cause:** security.md mandates every caught exception route through `shared/error_handler.py::sanitize_error` before reaching a client response; three routers don't:
  1. `aurabackend/api_gateway/routers/dashboards.py:196-208` `_run_tile`'s except clause sets `"error": str(exc)` directly — the executed text is caller-supplied SQL run through DuckDB. `dashboards.py` never imports `sanitize_error`, unlike its siblings `etl.py`/`files.py`/`chat.py`/`queries.py`.
  2. `aurabackend/insights_service/main.py:104-126` (`/analyze`, `/chart-suggestions`) puts `str(e)` directly into `HTTPException(detail=...)`.
  3. `aurabackend/connectors/main.py` (8 call sites: lines 118-123, 152-156, 246-250, 306-310, 388-392, 452-456, 482-486, 506-510) does the same for connector test/query errors, which can carry DB credentials/hostnames.
- **Caused by:** none — pre-existing; `counterfactual_service/main.py` already follows the correct pattern as the reference sibling.
- **Fix:** imported and routed every caught exception in all three files through `sanitize_error`, matching `etl.py`/`files.py`/`counterfactual_service/main.py`: `dashboards.py`'s `_run_tile` except clause, `insights_service/main.py`'s `/analyze` and `/chart-suggestions`, and all 8 `connectors/main.py` call sites (connector test, list-tables, ingest, introspect, execute-query, vault query/vector-search/spatial-query — the two `NotImplementedError` handlers at 481/505 were left as-is, they're not the leaking-raw-text path). Verified: `aurabackend/tests/test_dashboards_persistence.py`, `test_insights.py`, `test_connectors.py`, `test_connectors_duckdb_spatial.py`, `test_connectors_faiss.py` all still pass.

## BUG-025: mapek_worker.py mislabels a real canary-shim failure as "no routes yet"; financial_auditor.py skips its own overflow guard for two checks
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** degrades-accuracy.
- **Root cause:** two independent bugs:
  1. `aurabackend/uasr/mapek_worker.py:339-344` wraps `self._shim_router.apply(...)` in `except Exception: pass  # no routes yet — use raw rows`. But `ShimRouter.apply()` already returns a normal dict (never raises) for both "no routes" cases — the only way it raises is a genuine canary-shim `transform()` failure on live batch rows, which this bare except then swallows with no log/metric/event while misnaming the cause, directly the anti-pattern CLAUDE.md warns about ("a handler that maps a broad exception to one narrated reason will eventually lie").
  2. `aurabackend/agents/specialists/financial_auditor.py`'s `execute_as2305_analytical_procedures` (line 193) and `execute_as2401_fraud_detection` (line 310) read raw `entry.get("amount", 0)` and compare it directly (`>`, `%`, `abs()`), unlike the same file's `_money()` helper (used correctly two other places in the same file) built specifically to swallow non-numeric amounts instead of raising — a single bad row (plausible from arbitrary uploaded ledger data) crashes the entire audit batch with no try/except anywhere in `run_full_audit`'s call chain.
- **Caused by:** none — pre-existing.
- **Fix:** replaced the bare except in `mapek_worker.py:339-344` with a `logger.warning` naming the real cause (canary-shim transform failure) plus an `await self._emit("monitor", ...)` call, matching the module's existing event mechanism (used elsewhere for analyze/other phases) — raw rows are still used as the fallback, but the failure is no longer silent. Routed both `financial_auditor.py` amount reads (AS 2305 line ~193, AS 2401 line ~310) through `_money()` (returned as `float(...)` to stay compatible with the surrounding float-typed materiality thresholds and expectations, since `Decimal`/`float` arithmetic raises `TypeError` on subtraction). Tests: `aurabackend/tests/test_financial_auditor_bug025.py` (new — 3 tests: a non-numeric AS-2305 amount no longer crashes the batch, a non-numeric/missing AS-2401 amount no longer crashes duplicate/round-dollar detection, and `run_full_audit` survives a `NaN` row); full `test_materiality.py`, `test_mapek_shim_router_integration.py`, and the other `test_mapek_*` files still pass.

## BUG-026: ToolRegistry.call() never enforces requires_approval/is_destructive despite the flags existing to prevent unattended destructive actions
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature (safety) — a future tool registered with `requires_approval=True` would run unattended.
- **Root cause:** `aurabackend/agents/tool_registry.py`'s docstring promises "permission checks for free"; `Tool.requires_approval`/`is_destructive` (lines 21-22) are defined, but `ToolRegistry.call()` (line 86) goes straight from lookup to `await tool.fn(**kwargs)` — the flags are read only for display badges in `describe_tools()`, never as a gate. Compounding this, `agents/tools.py`'s `execute_sql` tool hardcodes `"approved": True` in its payload to the execution sandbox, so the one place downstream that does check an approval flag (`execution_sandbox_service/main.py`'s `if not job.approved: raise HTTPException(403,...)`) is unconditionally satisfied by the tool itself.
- **Caused by:** none — pre-existing.
- **Fix:** `aurabackend/agents/tool_registry.py`'s `ToolRegistry.call()` now takes a keyword-only `approved: bool = False` and gates before ever reaching `tool.fn`: when `tool.requires_approval or tool.is_destructive` and `not approved`, it records a `ToolCallRecord(approved=False, output=None)` and raises a new `ToolApprovalRequiredError` (a `PermissionError` subclass) instead of executing — enforced ahead of the `dry_run` branch too, so dry-run can't be used to route around it. Once past the gate, the resolved approval status is forwarded into `tool.fn`'s kwargs via `inspect.signature` (only when the function declares an `approved` parameter, so it's a no-op for the other ~8 registered tools). `agents/tools.py`'s `execute_sql` now takes `approved: bool = True` and forwards it into the sandbox payload instead of a hardcoded literal — no longer a blind constant, though its default still preserves today's behavior since `execute_sql` itself is not flagged `requires_approval`/`is_destructive` (deliberate: flipping that flag would gate all ~12 existing agent call sites that call it unapproved, which is a much larger behavior change than this bug asked for — left as an explicit follow-up decision, not bundled in silently). **Judgment call:** used a simple synchronous reject-with-message (`ToolApprovalRequiredError`) rather than routing through UASR's `PENDING_APPROVAL`/reaper queue (BUG-013's precedent) — that queue is a persistent, timeout-driven, async human-in-the-loop mechanism built for long-running self-healing repairs; `ToolRegistry.call()` is a synchronous in-process call from an agent loop with no natural "wait for a human" point, so a synchronous gate is the better shape here per the task's own fallback guidance. Tests: `aurabackend/tests/test_tool_registry_approval.py` (new) — proves a `requires_approval=True`/`is_destructive=True` tool raises and does not execute (dry-run included), executes once `approved=True` is passed, unflagged tools are unaffected, and the resolved approval status is forwarded to a tool function shaped like `execute_sql`.

## BUG-027: counterfactual engine.py's signing path ignores the admin key-revocation flag
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** blocks-feature (compliance) — `POST /counterfactual/admin/revoke-key` does not actually stop the revoked key from signing new fair-lending/hiring/insurance audit certificates.
- **Root cause:** `aurabackend/counterfactual_service/engine.py:1812`'s `run_job()` calls `signing.sign_bytes(...)` unconditionally, with no `cryptography.is_revoked()` check — unlike `financial_report.py:103`, which explicitly documents and implements "refuses to sign if the active key is revoked." `main.py:1101`'s `get_sth` (Merkle-tree-head signing) has the same gap. The revoked flag is only ever read in two places (the JWKS endpoint, and `financial_report.py`) — never in the primary counterfactual-audit product this service exists for.
- **Caused by:** none — pre-existing; the revocation mechanism itself works, it's just not consulted by these two signing paths.
- **Fix:** mirrored `financial_report.py:103`'s exact contract (`sig = None if cryptography.is_revoked() else signing.sign_bytes(canonical)`) in both places: `aurabackend/counterfactual_service/engine.py`'s `run_job()` (added a `cryptography` import; the existing `if sig_b64 is not None: ... signature_status = "signed" else: signature_status = "unsigned"` branch below it needed no change — it already implements the same "None means unsigned" contract `financial_report.py` and `main.py`'s `STHResponse` use) and `aurabackend/counterfactual_service/main.py`'s `get_sth()` (already imported `cryptography`; `signature_status`/`signature_b64`/`signing_key_source` were already conditioned on `sig_b64` truthiness, so no downstream change needed there either). Tests: `aurabackend/tests/test_counterfactual_sprint9.py::test_run_job_refuses_to_sign_with_a_revoked_key` (new) proves a revoked key makes `run_job()` return `signature_status="unsigned"`/`signature_b64=None`, mirroring `test_financial_audit.py::test_revoked_key_yields_unsigned`'s pattern; `aurabackend/tests/test_counterfactual_sth_revocation.py` (new) proves the same for `get_sth()`, plus a control test that signing still succeeds when the key is not revoked.

## BUG-028: frontend — chat errors collapse to one message; a UASR panel re-fires its fetch on every parent re-render
- **Status:** fixed
- **Found by:** codebase-quality-audit workflow, 2026-09-02.
- **Severity:** cosmetic / degrades-accuracy (not a backend/security issue).
- **Root cause:** two independent frontend bugs:
  1. `frontend/src/workbench/Workbench.tsx:281-293`'s chat handler wraps `chatService.streamMessage(...)` in a bare `catch {}` with no parameter, collapsing every failure mode (expired JWT, 5xx, network abort, JSON parse error) into one hardcoded "Commander offline" message — a user hitting an auth failure is told to "connect a gateway" instead of to re-authenticate.
  2. `frontend/src/terminal/panels/PipelinePanel.tsx:305`'s `UasrRecoveries` component does `if (!loaded) { void load(); }` directly in the render body instead of inside a `useEffect` — since the parent re-renders every ~8s telemetry tick/SSE event and `loaded` only flips once the async fetch resolves, any re-render landing before that resolution re-fires a duplicate `GET /uasr/recovery/pending`. Self-corrects once `loaded` becomes true; not a hang, just redundant requests.
- **Caused by:** none — pre-existing.
- **Fix:** (1) `Workbench.tsx` — gave the `catch` a parameter and added a `describeChatError` helper that inspects `chatService.streamMessage`'s thrown `Error` (it throws plain `Error`s: `stream failed: ${status}` on a non-ok HTTP response, `commander_disabled` on 404, or whatever `fetch` itself throws for a network failure/abort — no typed status). 401/403 now surfaces "Your session has expired — please sign in again to continue.", 5xx surfaces a "temporarily unavailable" message, `AbortError` surfaces "Request cancelled.", and everything else (network failure, 404/disabled) keeps the original "Commander offline" copy. (2) `PipelinePanel.tsx` — moved `UasrRecoveries`'s lazy load into `useEffect(() => { void load(); }, [load])` (mount-only; `load` is a stable `useCallback([])` reference), matching the fetch-on-mount pattern already used elsewhere in the file; the now-unused `loaded` read was dropped, keeping the state write for the async `finally` but nothing reads it anymore since the render-body guard is gone.
  Tests: `frontend/src/workbench/Workbench.test.tsx` (new `describe('Ask AURA chat error branching (BUG-028)')` block — asserts a 401 produces the re-auth message, a network `TypeError` produces the offline message, and the two differ) and the new `frontend/src/terminal/pipeline/__tests__/PipelinePanel.uasr.test.tsx` (selects the UASR node, forces a parent re-render while `healingService.pending()` is still an unresolved promise, and asserts it was called exactly once before and after resolution).
  Verified: `npx tsc --noEmit`, `npx eslint src --max-warnings 0`, `npx vitest run` (67 files / 311 tests) all clean from `frontend/`.

## BUG-029: lower-priority items from the 2026-09-02 codebase-quality audit (logged, not yet triaged for a fix)
- **Status:** open — logged so these don't evaporate; lower severity than BUG-016 through BUG-028, deferred.
- **Found by:** codebase-quality-audit workflow, 2026-09-02 (7 low-severity findings, not run through the adversarial verify pass — that pass was reserved for medium/high findings; treat these as plausible, not confirmed).
- **Severity:** low (cosmetic/duplication/readability) except where noted.
- **Findings:**
  1. `aurabackend/connectors/main.py:324` — `_connection_store` (meant to map `connection_id` → connector config) is never written anywhere in the codebase; every `/connections/{id}/query` call silently falls through to one global env-configured DB regardless of `connection_id`. **Severity: production-gap, medium** — worth the same treatment as BUG-010's UNION step (raise a clear error instead of silently misrouting) if not implementing real per-connection routing.
  2. `aurabackend/ingestion_service/kafka_client.py:27` — no application-level idempotency/dedup key check before publish; `enable_idempotence=True`'s comment overstates what it actually guarantees (only dedups broker-retries within one send call, not two independent client re-POSTs of the same `batch_id`). Same "ingestion_service doesn't start in this deployment" mitigating caveat as BUG-010.
  3. `aurabackend/shared/file_service.py:82` — `FileService.save_file`/`process_file` are dead in production (the live `/upload` route uses `shared/storage`+`shared/data_utils.py` instead) but still exist, untested-by-integration, and would violate the async-safety rule if ever wired in (`process_file` runs blocking pandas I/O with no `to_thread`).
  4. `aurabackend/evolution/api.py:240` — `feedback_summary()` does `__import__("datetime").datetime.now(...)` instead of just adding `datetime` to the existing `from datetime import timedelta, timezone` two lines above.
  5. `frontend/src/pages/PipelinesPanel.tsx:868` — the AI-pipeline-run and visual-builder result blocks are near-duplicate ~70-line JSX structures that should share one `<PipelineResultCard>` component.
  6. `frontend/src/pages/PipelinesPanel.tsx:372` — nine unconditional `console.log`/`console.error` calls dump full pipeline payloads (including AI prompts) to the browser console in production, not gated behind a dev flag.
  7. `frontend/src/workbench/Workbench.tsx:90` — 873-line single component combining shell chrome, chat, forensic-audit, healing-queue approvals, and the live radar model; each concern is independently extractable but the merge into "one shell" was noted as a deliberate earlier design choice per the file's own comments — a real maintainability cost but not a bug, and a large refactor out of scope for a surgical fix.
- **Caused by:** none — pre-existing across a long project history.
- **Fix:** none yet — item 1 is worth prioritizing (mirrors BUG-010's precedent); items 2-3 have the same "currently unreachable service" mitigation as BUG-010's items 2 and 4; items 4-7 are readability/duplication nits, pick up opportunistically.
