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
- **Status:** root-caused (2026-09-01). The `uasr.mapek_worker` /
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

## BUG-010: four undocumented silent-stub call sites (zero-stub-compliance audit)
- **Status:** open — requires a product decision (raise vs. implement vs.
  document), not a mechanical fix. Filed so it doesn't evaporate as an
  unlogged agent report, per this registry's own process.
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
- **Fix:** none yet. Item 1 (the UNION pipeline step) is the only one
  reachable in the current deployment and is the one worth prioritizing
  if this is picked up — either implement UNION support or make the
  fall-through raise a clear error instead of silently dropping the step.
  Items 2-4 are honestly disclosed already or currently unreachable;
  revisit if the scheduler/ingestion services become reachable.
